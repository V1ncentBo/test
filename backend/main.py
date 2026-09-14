"""全域机器智能监控平台 - 主入口"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from contextlib import asynccontextmanager
import os
import asyncio

import time
from datetime import datetime, timedelta

from models.database import init_db, engine, SessionLocal, MachineInfo, AlertLog, AIAnalysisLog
import models.cmdb
import models.resource_cmdb
from routers import machines, metrics, alerts, ai, auth, register_node
from routers import settings, report_schedule, ai_settings, advanced_routes, batch1_routes
from routers.cmdb import router as cmdb_router
from routers.resource_cmdb import router as resource_cmdb_router
from routers.dbs import router as dbs_router, db_collector
from websocket.manager import ws_manager
from services.collector import metrics_service, COLLECTOR_STATS
from services.alert_service import alert_service, notify_alerts
from services.node_scraper import scrape_node
from services.snmp_scraper import scrape_snmp, scrape_port_ipmac
from services.pve_scraper import discover_nodes, get_node_status, list_vms
from services.pve_ssh import pve_ssh_resolve_ips


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    # 启动时初始化数据库
    init_db()
    print(f"[{datetime.now()}] 数据库初始化完成")

    # 启动大屏数据定时推送
    asyncio.create_task(dashboard_pusher())
    print(f"[{datetime.now()}] 大屏推送任务已启动")

    # 启动离线检测
    asyncio.create_task(offline_detector())
    print(f"[{datetime.now()}] 离线检测任务已启动")

    # 启动MySQL数据归档
    asyncio.create_task(mysql_archiver())
    print(f"[{datetime.now()}] MySQL归档任务已启动")

    # 启动定时报表调度器
    asyncio.create_task(schedule_checker())
    print(f"[{datetime.now()}] 定时报表调度器已启动")

    # 启动 node_exporter 采集器（pull 模式：主动从各设备 9100 拉取），兑现部署指南承诺
    asyncio.create_task(node_exporter_collector())
    print(f"[{datetime.now()}] node_exporter 采集任务已启动")

    # 启动 SNMP 采集器（交换机/路由器：主动从各设备 161 WALK ifTable），兑现部署指南 SNMP 方式承诺
    asyncio.create_task(snmp_collector())
    print(f"[{datetime.now()}] snmp 采集任务已启动")

    # 启动 PVE 采集器（Proxmox VE：主动从各宿主机 :8006 API 拉取节点状态 + 虚拟机清单/指标）
    asyncio.create_task(pve_collector())
    print(f"[{datetime.now()}] pve 采集任务已启动")

    # 启动数据库只读采集器（MySQL / ES 等，仅读不写，周期写入 db_metrics）
    asyncio.create_task(db_collector())
    print(f"[{datetime.now()}] 数据库采集任务已启动")

    yield

    # 关闭时清理
    metrics_service.close()
    print(f"[{datetime.now()}] 服务已关闭")


async def dashboard_pusher():
    """每5秒推送大屏概览数据 — 带30秒缓存减少数据库查询"""
    _cache = {"data": None, "ts": 0}
    while True:
        try:
            now_ts = datetime.now().timestamp()
            # 30秒内使用缓存
            if _cache["data"] and (now_ts - _cache["ts"]) < 30:
                await ws_manager.broadcast_dashboard(_cache["data"])
                await asyncio.sleep(5)
                continue

            db = SessionLocal()
            try:
                machines = db.query(MachineInfo).all()
                latest = metrics_service.query_all_latest()

                # 计算统计 —— 字段名与 REST /metrics/dashboard 的 DashboardStats 完全一致
                total_machines = len(machines)
                online_count = sum(1 for m in machines if m.online_status == "online")
                offline_count = total_machines - online_count
                physical_count = sum(1 for m in machines if m.device_type == "physical")
                vm_count = total_machines - physical_count
                cpu_vals = [v.get("cpu_percent", 0) for v in latest.values()]
                mem_vals = [v.get("memory_percent", 0) for v in latest.values()]
                disk_vals = [v.get("disk_percent", 0) for v in latest.values()]
                today_alerts = alert_service.get_today_alert_count(db)
                today = datetime.now().strftime("%Y-%m-%d")
                alert_machines = db.query(AlertLog.machine_id).filter(
                    AlertLog.created_at >= today
                ).distinct().count()

                data = {
                    "total_machines": total_machines,
                    "physical_count": physical_count,
                    "vm_count": vm_count,
                    "online_count": online_count,
                    "offline_count": offline_count,
                    "today_alerts": today_alerts,
                    "alert_machines": alert_machines,
                    "avg_cpu": round(sum(cpu_vals) / len(cpu_vals), 1) if cpu_vals else 0,
                    "avg_memory": round(sum(mem_vals) / len(mem_vals), 1) if mem_vals else 0,
                    "avg_disk": round(sum(disk_vals) / len(disk_vals), 1) if disk_vals else 0,
                    "timestamp": datetime.now().isoformat(),
                }
                await ws_manager.broadcast_dashboard(data)
            finally:
                db.close()
        except Exception as e:
            print(f"[DashboardPusher] Error: {e}")
        await asyncio.sleep(5)


async def offline_detector():
    """每30秒检测离线设备 — 基于内存中的最近采集时间戳（P1-8，免去逐台打 InfluxDB）。

    同时：设备从在线变离线时生成一条带冷却的告警（P0-3），使离线事件进入告警流/钉钉，
    而不只是 UI 上的一个红点。
    """
    while True:
        try:
            db = SessionLocal()
            try:
                threshold_ts = time.time() - 60  # 60秒无采集数据则判离线
                machines = db.query(MachineInfo).filter(
                    MachineInfo.monitor_enabled == True
                ).all()
                for m in machines:
                    last = metrics_service.get_last_seen(m.id)
                    offline = (last is None) or (last < threshold_ts)
                    if not offline:
                        continue
                    if m.online_status != "offline":
                        m.online_status = "offline"
                        m.updated_at = datetime.now()
                        db.commit()
                    # P0-3 设备离线告警（30分钟冷却，避免抖动重复）
                    try:
                        recent = db.query(AlertLog).filter(
                            AlertLog.machine_id == m.id,
                            AlertLog.alert_type == "device_offline",
                            AlertLog.status == "unresolved",
                            AlertLog.created_at >= datetime.now() - timedelta(minutes=30),
                        ).first()
                        if not recent:
                            alert = AlertLog(
                                machine_id=m.id, alert_type="device_offline",
                                alert_level="warning", metric_name="设备在线状态",
                                current_value=0, threshold_value=1,
                                message=f"[{m.name}] 设备离线（超过 60s 无采集数据）",
                                status="unresolved",
                            )
                            db.add(alert)
                            db.commit()
                            notify_alerts([alert], m, ws_manager)
                    except Exception as e:
                        print(f"[OfflineDetector] 离线告警异常 {m.ip}: {e}")
            finally:
                db.close()
        except Exception as e:
            print(f"[OfflineDetector] Error: {e}")
        await asyncio.sleep(30)



async def mysql_archiver():
    """每天凌晨2点清理30天前的旧数据"""
    import logging
    logger = logging.getLogger("archiver")
    while True:
        try:
            now = datetime.now()
            # 只在凌晨2:00-2:30执行
            if now.hour == 2 and now.minute < 30:
                db = SessionLocal()
                try:
                    # 7天前的未处理告警自动标记为已解决
                    resolve_cutoff = now - timedelta(days=7)
                    auto_resolved = db.query(AlertLog).filter(
                        AlertLog.created_at < resolve_cutoff,
                        AlertLog.status == "unresolved"
                    ).update({"status": "resolved", "resolved_at": now}, synchronize_session=False)

                    cutoff = now - timedelta(days=30)
                    # 清理旧告警
                    del_alerts = db.query(AlertLog).filter(
                        AlertLog.created_at < cutoff
                    ).delete(synchronize_session=False)
                    # 清理旧AI分析记录
                    del_ai = db.query(AIAnalysisLog).filter(
                        AIAnalysisLog.created_at < cutoff
                    ).delete(synchronize_session=False)
                    db.commit()
                    if del_alerts + del_ai > 0:
                        logger.info(f"Archived: {auto_resolved} auto-resolved (7d+), {del_alerts} alerts deleted, {del_ai} AI logs (30d+)")
                finally:
                    db.close()
            await asyncio.sleep(3600)  # 每小时检查一次
        except Exception as e:
            logger.error(f"MySQL archiver error: {e}")
            await asyncio.sleep(3600)



async def schedule_checker():
    while True:
        try:
            now = datetime.now()
            sched_file = "/app/data/report_schedules.json"
            if os.path.exists(sched_file):
                import json as _json
                with open(sched_file, "r") as f:
                    schedules = _json.load(f)
                for s in schedules:
                    if not s.get("enabled", True):
                        continue
                    nrs = s.get("next_run", "")
                    if not nrs:
                        continue
                    try:
                        nr = datetime.fromisoformat(nrs)
                    except:
                        try:
                            nr = datetime.strptime(nrs, "%Y-%m-%dT%H:%M:%S")
                        except:
                            continue
                    if nr <= now:
                        print(f"[Scheduler] Running: {s.get('name')} ({s.get('frequency')})")
                        try:
                            from routers.report_schedule import generate_report
                            generate_report(s)
                        except Exception as e:
                            print(f"[Scheduler] Failed: {e}")
            await asyncio.sleep(60)
        except Exception as e:
            print(f"[Scheduler] Error: {e}")
            await asyncio.sleep(60)


async def node_exporter_collector():
    """周期性从各监控设备的 node_exporter(:9100) 拉取指标写入 InfluxDB —— pull 模式。

    此处补齐 pull 采集器的告警闭环：此前 node_exporter 纳管的 Linux 主机因走 pull 旁路，
    完全不触发阈值告警；现写入成功后调用 check_and_alert，复用钉钉/邮件/WS 推送。
    """
    interval = int(os.getenv("NE_COLLECT_INTERVAL", "15"))
    timeout = int(os.getenv("NE_SCRAPE_TIMEOUT", "3"))
    stats = COLLECTOR_STATS["node"]
    while True:
        t0 = time.time()
        success = failed = 0
        try:
            db = SessionLocal()
            try:
                machines = db.query(MachineInfo).filter(
                    MachineInfo.monitor_enabled == True,
                    MachineInfo.device_type.notin_(["snmp", "pve"])
                ).all()
                if machines:
                    tasks = [asyncio.to_thread(scrape_node, m.ip, 9100, timeout) for m in machines]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for m, res in zip(machines, results):
                        if isinstance(res, Exception) or not res:
                            failed += 1
                            continue
                        data = {"machine_id": m.id}
                        for k_gb in ("disk_total_gb", "disk_used_gb", "swap_used_gb"):
                            bare = k_gb[:-3]
                            if k_gb in res:
                                res[bare] = res.pop(k_gb)
                        data.update(res)
                        if metrics_service.write_metrics(data):
                            success += 1
                            metrics_service.touch_last_seen(m.id)
                            if m.online_status != "online":
                                m.online_status = "online"
                                m.updated_at = datetime.now()
                                db.commit()
                            # pull 采集器此前缺失的告警检查
                            try:
                                alerts = alert_service.check_and_alert(db, m.id, data)
                                if alerts:
                                    notify_alerts(alerts, m, ws_manager)
                            except Exception as e:
                                print(f"[NodeExporterCollector] 告警异常 {m.ip}: {e}")
            finally:
                db.close()
        except Exception as e:
            print(f"[NodeExporterCollector] Error: {e}")
        dur = round(time.time() - t0, 2)
        stats.update({
            "last_cycle_devices": success + failed, "last_success": success,
            "last_failed": failed, "last_duration_sec": dur, "last_run": datetime.now().isoformat(),
        })
        metrics_service.write_collector_stats("node", cycle_devices=success + failed,
                                              success=success, failed=failed, duration_sec=dur)
        await asyncio.sleep(interval)


# SNMP 并发采集信号量：限制同时进行的 WALK 数量，避免大批量设备时打爆事件循环/交换机
_snmp_sem = None
def _get_snmp_sem():
    global _snmp_sem
    if _snmp_sem is None:
        _snmp_sem = asyncio.Semaphore(int(os.getenv("SNMP_MAX_CONCURRENCY", "16")))
    return _snmp_sem


async def _collect_snmp_device(m, timeout, retries, last_sample, cycle_count):
    """单台 SNMP 设备的采集：scrape → 速率计算 → 批量写端口 → 端口级告警 → IP/MAC 刷新。"""
    async with _get_snmp_sem():
        db = SessionLocal()
        try:
            # 在内层会话内重新查询该设备，确保后续对 online_status/name/remark 的修改
            # 能随内层 db.commit() 落库——调用方传入的 m 绑定外层会话，其改动不会被内层会话刷写。
            mm = db.query(MachineInfo).filter(MachineInfo.id == m.id).first()
            res = await asyncio.to_thread(
                scrape_snmp, m.ip, m.snmp_port or 161,
                m.snmp_community or "public", m.snmp_version or "v2c",
                timeout, retries
            )
            if not res or not res.get("online"):
                return None  # 不可达
            mid = m.id
            metrics_service.write_device_metrics(mid, True, res.get("sys_uptime", 0))
            changed = False
            if mm and ((not mm.name or mm.name == m.ip) and res.get("sys_name")):
                mm.name = res["sys_name"]; changed = True
            if mm and (res.get("sys_descr") and (not mm.remark or len(mm.remark or "") < 5)):
                mm.remark = res["sys_descr"][:240]; changed = True

            prev = last_sample.get(mid, {})
            now = time.time()
            cur = {}
            port_writes = []
            port_alert_inputs = []
            for p in res.get("ports", []):
                idx = p["index"]
                speed = p.get("speed", 0) or 0
                pdata = {
                    "ifOperStatus": p.get("oper_status", 0),
                    "ifSpeed": speed,
                    "ifInOctets": p.get("in_octets", 0),
                    "ifOutOctets": p.get("out_octets", 0),
                    "in_errors": p.get("in_errors", 0),
                    "out_errors": p.get("out_errors", 0),
                    "in_discards": p.get("in_discards", 0),
                    "out_discards": p.get("out_discards", 0),
                }
                pin, pout = p.get("in_octets", 0), p.get("out_octets", 0)
                pre = prev.get(idx)
                if pre:
                    dt = now - pre["t"]
                    if dt > 0:
                        din = max(pin - pre["in"], 0); dout = max(pout - pre["out"], 0)
                        pdata["in_mbps"] = round(din * 8 / dt / 1e6, 3)
                        pdata["out_mbps"] = round(dout * 8 / dt / 1e6, 3)
                        if speed > 0:
                            pdata["util_in"] = round(pdata["in_mbps"] / (speed / 1e6) * 100, 2)
                            pdata["util_out"] = round(pdata["out_mbps"] / (speed / 1e6) * 100, 2)
                cur[idx] = {"in": pin, "out": pout, "t": now}
                port_writes.append((idx, p.get("descr", ""), pdata))
                port_alert_inputs.append({
                    "index": idx, "name": p.get("descr", "") or f"ifIndex{idx}", "speed": speed,
                    "oper_status": p.get("oper_status", 0),
                    "util_in": pdata.get("util_in", 0), "util_out": pdata.get("util_out", 0),
                    "in_errors": p.get("in_errors", 0), "out_errors": p.get("out_errors", 0),
                    "in_discards": p.get("in_discards", 0), "out_discards": p.get("out_discards", 0),
                })
            # 批量写端口（P1-6：52 端口一次性写入）
            metrics_service.write_ports_batch(mid, port_writes)
            last_sample[mid] = cur
            metrics_service.touch_last_seen(mid)
            # 端口级告警（P0-2）
            try:
                alerts = alert_service.check_port_alerts(db, mid, port_alert_inputs, mm or m)
                if alerts:
                    notify_alerts(alerts, mm or m, ws_manager)
            except Exception as e:
                print(f"[SNMPCollector] {m.ip} 端口告警异常: {e}")
            # IP/MAC 映射（节流：每 10 轮 或 缓存为空）
            try:
                if (cycle_count % 10 == 0) or not metrics_service.get_port_ipmac(mid):
                    ipmac = await asyncio.to_thread(
                        scrape_port_ipmac, m.ip, m.snmp_port or 161,
                        m.snmp_community or "public", m.snmp_version or "v2c",
                        max(timeout * 2, 8), 2
                    )
                    metrics_service.update_port_ipmac(mid, ipmac)
            except Exception as e:
                print(f"[SNMPCollector] {m.ip} IP/MAC 采集异常: {e}")
            if changed:
                mm.updated_at = datetime.now()
            if mm:
                mm.online_status = "online"
                mm.updated_at = datetime.now()
            db.commit()
            return True
        except Exception as e:
            print(f"[SNMPCollector] {m.ip} 处理异常: {e}")
            return False
        finally:
            db.close()


async def snmp_collector():
    """周期性从各 SNMP 设备(:161) 并发 WALK ifTable，采集端口指标写入 InfluxDB。

    相比初版优化：
    - 并发扇出（asyncio.gather + 信号量），不再逐台串行等待；
    - 端口指标批量写，单台 52 端口的写请求从 53 次降到 1 次（collector.write_ports_batch）；
    - 离线设备指数退避，避免大批量不可达设备拖慢整轮采集；
    - 接入端口级告警与采集器健康指标。
    """
    interval = int(os.getenv("SNMP_COLLECT_INTERVAL", "30"))
    timeout = int(os.getenv("SNMP_SCRAPE_TIMEOUT", "3"))
    retries = int(os.getenv("SNMP_SCRAPE_RETRIES", "1"))
    last_sample = {}          # {machine_id: {port_index: {"in": int, "out": int, "t": float}}}
    down_backoff = {}         # {machine_id: {"next": ts, "attempt": int}}  离线退避（P1-7）
    cycle_count = 0
    stats = COLLECTOR_STATS["snmp"]
    while True:
        t0 = time.time()
        success = failed = skipped = 0
        try:
            db = SessionLocal()
            try:
                machines = db.query(MachineInfo).filter(
                    MachineInfo.monitor_enabled == True,
                    MachineInfo.device_type == "snmp"
                ).all()
                now = time.time()
                # 离线退避：跳过未到重试时间的设备（P1-7）
                due = [m for m in machines if down_backoff.get(m.id, {}).get("next", 0) <= now]
                skipped = len(machines) - len(due)
                tasks = [_collect_snmp_device(m, timeout, retries, last_sample, cycle_count) for m in due]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for m, res in zip(due, results):
                    if res is True:
                        success += 1
                        down_backoff.pop(m.id, None)
                        continue
                    # 不可达 / 异常：计入失败并按指数退避（30s → 600s 上限）
                    failed += 1
                    b = down_backoff.setdefault(m.id, {"next": 0, "attempt": 0})
                    b["attempt"] = min(b["attempt"] + 1, 6)
                    b["next"] = now + min(30 * (2 ** (b["attempt"] - 1)), 600)
            finally:
                db.close()
        except Exception as e:
            print(f"[SNMPCollector] Error: {e}")
        cycle_count += 1
        dur = round(time.time() - t0, 2)
        stats.update({
            "last_cycle_devices": success + failed + skipped, "last_success": success,
            "last_failed": failed, "last_skipped_down": skipped,
            "last_duration_sec": dur, "last_run": datetime.now().isoformat(),
        })
        metrics_service.write_collector_stats("snmp", cycle_devices=success + failed + skipped,
                                              success=success, failed=failed, skipped_down=skipped,
                                              duration_sec=dur)
        await asyncio.sleep(interval)


# PVE VM 网络速率计算用的上一次采样快照：{(host_id, vmid): (netin, netout, ts)}
PVE_VM_NET_PREV: dict = {}


# 每台 PVE 宿主机的 SSH 反查 IP 节流时间戳（避免每轮 60s 都 SSH）
PVE_SSH_LAST = {}


async def pve_collector():
    """周期性从 PVE 宿主机(:8006 HTTPS API)拉取节点状态 + 虚拟机清单/指标，写入 InfluxDB。

    设计要点：
    - 每台 device_type='pve' 且启用的宿主机，调 PVE API 拿节点状态(写宿主机 machine_metrics)
      与集群 VM 列表；
    - 每台 VM upsert 为 parent_id=宿主机的 pve_vm 子设备，并写 vm_metrics；复用 children 机制纳管；
    - 接入 touch_last_seen / 离线检测 / collector_stats；VM 停机会被标记为 offline 而非误判离线。
    """
    interval = int(os.getenv("PVE_COLLECT_INTERVAL", "60"))
    timeout = int(os.getenv("PVE_SCRAPE_TIMEOUT", "5"))
    stats = COLLECTOR_STATS["pve"]
    while True:
        t0 = time.time()
        success = failed = vm_count = n_hosts = 0
        try:
            db = SessionLocal()
            try:
                hosts = db.query(MachineInfo).filter(
                    MachineInfo.monitor_enabled == True,
                    MachineInfo.device_type == "pve"
                ).all()
                n_hosts = len(hosts)
                for h in hosts:
                    try:
                        base = f"https://{h.ip}:{h.pve_port or 8006}"
                        token = h.pve_token or ""
                        if not token:
                            failed += 1
                            continue
                        # 1) 节点列表（自动发现节点名，缓存到 pve_node）
                        nodes = discover_nodes(base, token, timeout)
                        node_name = h.pve_node or (nodes[0] if nodes else None)
                        if not node_name:
                            failed += 1
                            continue
                        if not h.pve_node:
                            h.pve_node = node_name
                        # 2) 宿主机节点状态 -> 写 machine_metrics（宿主机本身指标）
                        ns = get_node_status(base, token, node_name, timeout)
                        if ns:
                            mem = ns.get("memory") or {}
                            rootfs = ns.get("rootfs") or {}
                            host_metrics = {
                                "machine_id": h.id,
                                "cpu_percent": round((ns.get("cpu") or 0) * 100, 2),
                                "memory_used_gb": round((mem.get("used", 0)) / 1e9, 2),
                                "memory_total_gb": round((mem.get("total", 0)) / 1e9, 2),
                                "memory_percent": round((mem.get("used", 1)) / (mem.get("total", 1)) * 100, 2) if mem.get("total") else 0,
                                "disk_used_gb": round((rootfs.get("used", 0)) / 1e9, 2),
                                "disk_total_gb": round((rootfs.get("total", 0)) / 1e9, 2),
                                "disk_percent": round((rootfs.get("used", 1)) / (rootfs.get("total", 1)) * 100, 2) if rootfs.get("total") else 0,
                            }
                            if metrics_service.write_metrics(host_metrics):
                                metrics_service.touch_last_seen(h.id)
                                if h.online_status != "online":
                                    h.online_status = "online"
                                    h.updated_at = datetime.now()
                                success += 1
                            else:
                                failed += 1
                        else:
                            failed += 1
                        # 3) 集群 VM 列表 -> upsert 子设备 + 写 vm_metrics
                        vms = list_vms(base, token, timeout)
                        seen = set()
                        for v in vms:
                            vmid = int(v.get("vmid"))
                            vm_name = v.get("name") or f"vm-{vmid}"
                            status = v.get("status")
                            vm = db.query(MachineInfo).filter(
                                MachineInfo.parent_id == h.id,
                                MachineInfo.pve_vmid == vmid
                            ).first()
                            is_new = False
                            if not vm:
                                is_new = True
                                vm = MachineInfo(
                                    name=vm_name, ip="", device_type="pve_vm",
                                    parent_id=h.id, pve_vmid=vmid,
                                    monitor_enabled=True, online_status="unknown",
                                    group_name=h.group_name or "default",
                                )
                                db.add(vm)
                                db.flush()
                            # 已存在记录（如手动 kvm/vmware 机器）保留其名称/IP/类型，仅更新时间戳
                            if is_new:
                                vm.name = vm_name
                            vm.updated_at = datetime.now()
                            # 仅对纯 pve_vm 由 PVE 接管在线状态；手动机器交给 agent 管理
                            if vm.device_type == "pve_vm":
                                vm.online_status = "online" if status == "running" else "offline"
                            maxcpu = int(v.get("maxcpu") or 1)
                            cpu = float(v.get("cpu") or 0)
                            maxmem = int(v.get("maxmem") or 1)
                            mem = int(v.get("mem") or 0)
                            disk = int(v.get("disk") or 0)
                            maxdisk = int(v.get("maxdisk") or 0)
                            netin = int(v.get("netin") or 0)
                            netout = int(v.get("netout") or 0)
                            uptime = int(v.get("uptime") or 0)
                            disk_percent = round((disk / maxdisk) * 100, 2) if maxdisk else 0.0
                            # 网络速率：netin/netout 为自开机累计字节计数器，需两次采样求差换算 Mbps
                            net_prev = PVE_VM_NET_PREV.get((h.id, vmid))
                            _now = time.time()
                            net_in_mbps = net_out_mbps = 0.0
                            if net_prev:
                                _p_netin, _p_netout, _p_ts = net_prev
                                _dt = _now - _p_ts
                                if _dt > 0:
                                    if netin >= _p_netin:
                                        net_in_mbps = round((netin - _p_netin) / _dt * 8 / 1e6, 3)
                                    if netout >= _p_netout:
                                        net_out_mbps = round((netout - _p_netout) / _dt * 8 / 1e6, 3)
                            PVE_VM_NET_PREV[(h.id, vmid)] = (netin, netout, _now)
                            metrics_service.write_vm_metrics(vm.id, vmid, vm_name, {
                                "cpu_percent": round((cpu / maxcpu) * 100, 2) if maxcpu else round(cpu * 100, 2),
                                "cpu_cores": maxcpu,
                                "memory_percent": round((mem / maxmem) * 100, 2) if maxmem else 0,
                                "memory_used_bytes": mem,
                                "memory_total_bytes": maxmem,
                                "disk_bytes": disk,
                                "disk_total_bytes": maxdisk,
                                "disk_percent": disk_percent,
                                "network_in_mbps": net_in_mbps,
                                "network_out_mbps": net_out_mbps,
                                "status": 1 if status == "running" else 0,
                                "uptime_seconds": uptime,
                            })
                            if vm.device_type == "pve_vm":
                                metrics_service.touch_last_seen(vm.id)
                            vm_count += 1
                            seen.add(vmid)
                        db.commit()
                        # 4) 清理已被删除的 VM（停止纳管但保留记录）
                        removed = db.query(MachineInfo).filter(
                            MachineInfo.parent_id == h.id,
                            MachineInfo.device_type == "pve_vm",
                            MachineInfo.pve_vmid.notin_(list(seen)) if seen else MachineInfo.pve_vmid.isnot(None),
                        ).all() if seen else db.query(MachineInfo).filter(
                            MachineInfo.parent_id == h.id,
                            MachineInfo.device_type == "pve_vm",
                        ).all()
                        if removed:
                            for rv in removed:
                                rv.monitor_enabled = False
                                rv.online_status = "offline"
                                rv.updated_at = datetime.now()
                            db.commit()
                        # 5) 通过 SSH 反查纯 pve_vm 子机真实 IP（绕过 API token 权限墙，每 5 分钟一次）
                        _now = time.time()
                        if (_now - PVE_SSH_LAST.get(h.id, 0)) >= 300:
                            try:
                                _cnt, _detail = pve_ssh_resolve_ips(h)
                                if _cnt:
                                    print(f"[PVECollector] {h.ip} SSH 反查 IP 更新 {_cnt} 台 VM")
                                PVE_SSH_LAST[h.id] = _now
                            except Exception as e:
                                print(f"[PVECollector] {h.ip} SSH 反查 IP 失败: {e}")
                    except Exception as e:
                        print(f"[PVECollector] {h.ip} 处理异常: {e}")
                        failed += 1
            finally:
                db.close()
        except Exception as e:
            print(f"[PVECollector] Error: {e}")
        dur = round(time.time() - t0, 2)
        stats.update({
            "last_cycle_devices": n_hosts, "last_success": success,
            "last_failed": failed, "last_vm_count": vm_count,
            "last_duration_sec": dur, "last_run": datetime.now().isoformat(),
        })
        metrics_service.write_collector_stats("pve", cycle_devices=n_hosts,
                                              success=success, failed=failed,
                                              vm_count=vm_count, duration_sec=dur)
        await asyncio.sleep(interval)


# ===== FastAPI 应用 =====
app = FastAPI(
    title="全域机器智能监控平台",
    description="物理机 + 虚拟机统一监控 + AI大模型智能分析",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 跨域 — 默认关闭（前端经 nginx 同源访问不需要 CORS），.env 设 CORS_ORIGIN 按需开启
cors_origin = os.environ.get("CORS_ORIGIN", "")
if cors_origin:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origin.split(","),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# API 限流 — 全局200次/分钟，登录5次/分钟
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# 注册路由
app.include_router(machines.router)
app.include_router(metrics.router)
app.include_router(alerts.router)
app.include_router(ai.router)
app.include_router(auth.router)
app.include_router(settings.router)
app.include_router(report_schedule.router)
app.include_router(ai_settings.router)
app.include_router(advanced_routes.router)
app.include_router(batch1_routes.router)
app.include_router(register_node.router)
app.include_router(cmdb_router)
app.include_router(resource_cmdb_router)
app.include_router(dbs_router)


@app.get("/api/install-agent.sh")
async def install_agent_script(request: Request):
    """动态生成 Agent 安装脚本：后端地址由请求 URL 推导，不再写死 IP。

    被监控机执行 `curl -s http://<本平台>/api/install-agent.sh | bash -s <设备ID>`，
    脚本里的 BK 变量会自动指向当前平台的访问地址（含协议/端口），迁移机器无需改代码。
    """
    import os
    script_path = os.path.join(os.path.dirname(__file__), "data", "install_agent.sh")
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            template = f.read()
    except FileNotFoundError:
        return PlainTextResponse("# install_agent.sh not found on server", status_code=404)

    # 从请求推导平台对外地址（协议 + host:port），与用户访问平台所用的地址一致
    base = f"{request.url.scheme}://{request.url.netloc}"
    script = template.replace("__BK__", base)
    return PlainTextResponse(script, media_type="text/plain; charset=utf-8")



# ===== WebSocket 端点 =====
@app.websocket("/ws/machine/{machine_id}")
async def ws_machine(websocket: WebSocket, machine_id: str):
    """单设备实时监控 WebSocket"""
    await ws_manager.connect_machine(websocket, machine_id)
    try:
        while True:
            await websocket.receive_text()  # 保持连接
    except WebSocketDisconnect:
        ws_manager.disconnect_machine(websocket, machine_id)


@app.websocket("/ws/dashboard")
async def ws_dashboard(websocket: WebSocket):
    """大屏实时推送 WebSocket"""
    await ws_manager.connect_dashboard(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect_dashboard(websocket)


@app.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket):
    """告警实时推送 WebSocket"""
    await ws_manager.connect_alerts(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect_alerts(websocket)


@app.get("/")
def root():
    return {
        "name": "全域机器智能监控平台",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/api/health")
def health():
    return {"status": "healthy", "time": datetime.now().isoformat()}
