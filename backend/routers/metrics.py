"""监控数据 API 路由"""

from fastapi import APIRouter, Depends, HTTPException, Query

from sqlalchemy.orm import Session

from typing import List

from models.database import get_db, MachineInfo, AlertLog

from routers.auth import get_current_user, require_any_perm

from models.schema import MetricData, DashboardStats

from services.collector import metrics_service

from services.cache import ttl_cache, attl_cache

from services.alert_service import alert_service

from services.dingtalk import send_alert as dingtalk_send

from services.email_service import send_alert as email_send

from websocket.manager import ws_manager

from datetime import datetime

import asyncio

import re

import logging

logger = logging.getLogger("metrics")



router = APIRouter(prefix="/api/metrics", tags=["监控数据"], )

# 监控大屏：独立公开只读入口。不要给 public_router 挂 get_current_user；
# 返回体由 public_screen 显式挑选字段，严禁直接序列化 ORM 对象。
public_router = APIRouter(prefix="/api/metrics/public", tags=["监控大屏·公开只读"])


# ══ HWSTATIC-20261010：机器「固定属性」快照 ══════════════════════════════════
# 详情页下方卡片里这些字段对一台机器是**恒定的**（不随时间变化），但它们的可获取性
# 取决于「有没有采集器在持续上报」：拉模型（宿主机 API / node_exporter）拿不到绝对值，
# 跨网段/离线机器也可能长期拿不到 ⇒ 详情页只能显示 `-`。
# 方案：agent 在**首次识别**时（其实每轮都带，首次即落库）上报 → 落 MySQL machine_info；
# 详情页 /latest 在实时数据缺失时回填。重新运行一次安装脚本即可刷新。
#   (machine_info 列, MetricData/InfluxDB 字段名, 类型)
# ⚠ `cpu_freq_mhz` **不**在快照集合里：agent 取的是 `scaling_cur_freq`（当前频率，随负载
#   变化），不是固定属性，快照下来反而误导。
_HW_FIELDS = [
    ("hw_cpu_cores", "cpu_cores", int),
    ("hw_memory_total_gb", "memory_total", float),
    ("hw_disk_total_gb", "disk_total", float),
    ("hw_swap_total_gb", "swap_total", float),
    ("hw_net_link_speed", "net_link_speed", int),
    ("hw_file_handles_max", "file_handles_max", int),
]
# /api/metrics/latest 回填用：machine_info 列 -> 返回体字段名（详情页读的名字）
_HW_FALLBACK = [
    ("hw_cpu_cores", "cpu_cores"),
    ("hw_memory_total_gb", "memory_total_gb"),
    ("hw_disk_total_gb", "disk_total_gb"),
    ("hw_swap_total_gb", "swap_total_gb"),
    ("hw_net_link_speed", "net_link_speed"),
    ("hw_file_handles_max", "file_handles_max"),
]


def _save_hw_static(machine, payload: dict) -> bool:
    """把「固定属性」写入 machine_info（HWSTATIC）。

    ⛔ 只在值 **有效且 > 0** 时覆盖 —— 一次采集失败产生的 0 绝不能覆盖已有真实值。
    返回值 = 本次是否有**至少一项有效值**被读到，用于刷新 hw_collected_at。

    语义（重要）：hw_collected_at = **最近一次成功读取到固定属性的时间**，
    不是「值发生变化的时间」。理由：
      · 重跑安装脚本 / 常规上报（每 15s）都会重新读一次值 ⇒ 时间戳应跟着走，
        运维才知道「页面上这个总容量是多少秒之前测到的」；
      · 若只在值变化时刷新，重跑脚本后时间戳纹丝不动，运维会误以为脚本没生效。
    """
    touched = False
    for col, src, cast in _HW_FIELDS:
        v = payload.get(src)
        if v is None:
            continue
        try:
            v = cast(v)
        except (TypeError, ValueError):
            continue
        if v and v > 0:
            # ⛔ NEFIX-20261013 范围守卫：部分系统的 fs.file-max 为 unsigned 64-bit 上限
            #   (18446744073709551615)，超出 MySQL BIGINT(有符号, max 2^63-1) 范围 ⇒
            #   INSERT 报 (1264) Out of range 并使整个事务回滚（连带整轮 collector 失败）。
            #   超范围的哨兵值一律丢弃，绝不写库、也不覆盖已有真实值。
            if cast is int and v > 9223372036854775807:
                continue
            old = getattr(machine, col, 0) or 0
            # 浮点列做容差比较：容量以 GB 计（整数 GB 级别），0.05 的容差足以吸收
            # 往返序列化噪声，又不会掩盖真实的扩盘（≥1GB 的差异）；只为少写一次 UPDATE。
            same = (abs(float(old) - float(v)) < 0.05) if cast is float else (old == v)
            if not same:
                setattr(machine, col, v)
            touched = True
    return touched





@router.post("/report")

async def report_metrics(data: MetricData, db: Session = Depends(get_db)):

    """Agent 上报监控数据"""

    success = metrics_service.write_metrics(data.model_dump())

    if success:

        metrics_service.touch_last_seen(data.machine_id)



    # 更新在线状态

    machine = db.query(MachineInfo).filter(MachineInfo.id == data.machine_id).first()

    if machine:

        machine.online_status = "online"

        machine.updated_at = datetime.now()

        if hasattr(data, "top_processes") and data.top_processes:

            machine.top_processes = data.top_processes

        # HWSTATIC-20261010：固定属性快照落库（首次上报即写入；重复运行安装脚本可刷新）
        if _save_hw_static(machine, data.model_dump()):

            machine.hw_collected_at = datetime.now()

        db.commit()



    # 检查告警

    alerts = []

    if success:

        alerts = alert_service.check_and_alert(db, data.machine_id, data.model_dump())

        for alert in alerts:

            try:

                await ws_manager.broadcast_alert({

                    "id": alert.id,

                    "machine_id": alert.machine_id,

                    "alert_type": alert.alert_type,

                    "alert_level": alert.alert_level,

                    "message": alert.message,

                    "created_at": alert.created_at.isoformat(),

                })

            except Exception:

                pass

            # 钉钉推送

            try:

                if machine:

                    dingtalk_send(

                        machine_name=machine.name,

                        machine_ip=machine.ip,

                        machine_id=machine.id,

                        alert_type=alert.alert_type,

                        alert_level=alert.alert_level,

                        metric_name=alert.metric_name,

                        current_value=alert.current_value,

                        threshold_value=alert.threshold_value,

                    )

            except Exception:

                pass

            # 邮件推送

            try:

                if machine:

                    email_send(

                        machine_name=machine.name,

                        machine_ip=machine.ip,

                        machine_id=machine.id,

                        alert_type=alert.alert_type,

                        alert_level=alert.alert_level,

                        metric_name=alert.metric_name,

                        current_value=alert.current_value,

                        threshold_value=alert.threshold_value,

                    )

            except Exception:

                pass



    # WebSocket 实时推送

    try:

        await ws_manager.broadcast_metrics(str(data.machine_id), data.model_dump())

    except Exception:

        pass



    return {"status": "ok", "alerts_triggered": len(alerts)}





@router.post("/report/batch")

async def batch_report(metrics_list: List[MetricData], db: Session = Depends(get_db)):

    """批量上报"""

    count = metrics_service.batch_write([m.model_dump() for m in metrics_list])

    return {"status": "ok", "written": count}





@router.get("/latest/{machine_id}", dependencies=[Depends(require_any_perm("machines", "monitor"))])

def get_latest(machine_id: int, db: Session = Depends(get_db)):

    """获取设备最新指标"""

    data = metrics_service.query_latest(machine_id)

    if not data:

        raise HTTPException(status_code=404, detail="暂无监控数据")

    # 注入进程TOP5数据 (存储于 machine_info 表)

    try:

        machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()

        if machine and machine.top_processes:

            data["top_processes"] = machine.top_processes

        # HWSTATIC-20261010：用「固定属性」快照回填实时数据里缺失/为 0 的字段。
        # 典型场景：pve_vm / vmware 等由宿主机 API 采集的机器拿不到 disk_total_gb、
        # 跨网段机器长期没有实时上报 ⇒ 总容量等恒定量不应显示成 `-`。
        if machine:

            for col, dst in _HW_FALLBACK:

                snap = getattr(machine, col, 0) or 0

                if snap and not data.get(dst):

                    data[dst] = snap

            if machine.hw_collected_at:

                data["hw_collected_at"] = machine.hw_collected_at.strftime("%Y-%m-%d %H:%M:%S")

    except Exception:

        pass

    # ══ HWSTATIC-SPLIT-20261013：固定属性 vs 实时数据 分栏 ════════════════════
    # 详情页「系统状态」原来把固定属性(CPU核数/总容量/网卡速率/句柄上限)和实时数据
    # (CPU%/内存%/负载/进程数)混在同一个 data 里平铺，用户看不出哪些恒定、哪些实时。
    # 这里在返回体里**显式拆出 static 分组**，前端据此分栏 + 加快照时间标签。
    # ⚠ 兼容：metrics 里仍保留原字段（不动既有详情页渲染），只**新增** static 字段。
    static_fields = {}
    for col, dst in _HW_FALLBACK:
        snap = getattr(machine, col, 0) or 0 if machine else 0
        if snap:
            static_fields[dst] = snap
    if machine and machine.hw_collected_at:
        static_fields["hw_collected_at"] = machine.hw_collected_at.strftime("%Y-%m-%d %H:%M:%S")
    # NEFIX-20261013：pull(node_exporter) 机器的 hw_* 由 node_scraper 回写、**不写 hw_collected_at**
    #   （该列是「本机有 agent push」的判据，见 main.py DUPDIAG），故时间标签回退到 updated_at，
    #   让所有机器（含 node_exporter）都能显示「采集于 …」。agent 机器仍优先用 hw_collected_at。
    elif machine and machine.updated_at and static_fields:
        static_fields["hw_collected_at"] = machine.updated_at.strftime("%Y-%m-%d %H:%M:%S")

    return {"machine_id": machine_id, "metrics": data, "static": static_fields}





@router.get("/history/{machine_id}", dependencies=[Depends(require_any_perm("machines", "monitor"))])

def get_history(

    machine_id: int,

    metric: str = Query(..., description="指标名称"),

    range: str = Query("-1h", description="时间范围"),

    step: str = Query("auto", description="聚合窗口"),

):

    """获取指标历史曲线数据"""

    points = metrics_service.query_metrics(machine_id, metric, start=range, step=step)

    return {

        "machine_id": machine_id,

        "metric": metric,

        "data": points,

    }





# ========== SNMP 网络设备端口 API ==========

@router.get("/snmp-ports-summary", dependencies=[Depends(require_any_perm("machines", "monitor"))])

# PERF-20260915：单台设备的 query_port_latest 实测约 0.4~0.6s（Flux last() 跨 ~52 端口序列）。
# 端口数据 30s 才更新一次，10s 内复用完全安全，可把设备列表页的重复刷新成本降到 ~0。
@attl_cache(ttl=10)

async def snmp_ports_summary(db: Session = Depends(get_db)):

    """所有 SNMP 设备的端口 UP/total 汇总，供设备管理列表「端口状态」列展示"""

    devices = db.query(MachineInfo).filter(

        MachineInfo.device_type == "snmp",

        MachineInfo.monitor_enabled == True,

    ).all()

    out = {}

    for d in devices:

        # PERF-20260915：query_port_latest 实测约 0.5s（Flux last() 跨 52 端口序列）。
        # 该端点是 async，原先同步调用会把事件循环冻住 0.5s —— 任何用户打开设备页，
        # 全站请求都被拖住。卸载到线程（纯 Influx 读，无 session）。
        ports = await asyncio.to_thread(metrics_service.query_port_latest, d.id)

        total = len(ports)

        up = sum(1 for p in ports.values() if (p.get("ifOperStatus") or 0) == 1)

        down_ports = [p.get("port_name") for p in ports.values()

                      if (p.get("ifOperStatus") or 0) != 1 and p.get("port_name")]

        out[str(d.id)] = {"total": total, "up": up, "down_ports": down_ports}

    return out





@router.get("/device/{machine_id}/ports", dependencies=[Depends(require_any_perm("machines", "monitor"))])

# PERF-20260915：同上，端口表 10s 内复用（数据本身 30s 才刷新一次）
@attl_cache(ttl=10)

async def device_ports(machine_id: int, db: Session = Depends(get_db)):

    """单台 SNMP 设备端口最新状态表（详情页端口视图用）"""

    device = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()

    if not device:

        raise HTTPException(status_code=404, detail="设备不存在")

    ports = await asyncio.to_thread(metrics_service.query_port_latest, machine_id)

    ipmac = metrics_service.get_port_ipmac(machine_id)

    rows = []

    for idx, p in ports.items():

        speed = int(p.get("ifSpeed") or 0)

        in_mbps = float(p.get("in_mbps") or 0)

        out_mbps = float(p.get("out_mbps") or 0)

        try:

            pm = ipmac.get(int(str(idx)), [])

        except (ValueError, TypeError):

            pm = ipmac.get(idx, [])

        ips = "; ".join(x.get("ip", "") for x in pm)

        macs = "; ".join(x.get("mac", "") for x in pm)

        rows.append({

            "index": idx,

            "name": p.get("port_name", ""),

            "speed": speed,

            "oper_status": int(p.get("ifOperStatus") or 0),

            "in_mbps": in_mbps,

            "out_mbps": out_mbps,

            "in_errors": int(p.get("in_errors") or 0),

            "out_errors": int(p.get("out_errors") or 0),

            "in_discards": int(p.get("in_discards") or 0),

            "out_discards": int(p.get("out_discards") or 0),

            "ip": ips,

            "mac": macs,

        })

    rows.sort(key=lambda r: int(r["index"]) if str(r["index"]).isdigit() else 0)

    return {"device_type": device.device_type, "ports": rows}





@router.get("/device/{machine_id}/port-history", dependencies=[Depends(require_any_perm("machines", "monitor"))])

async def device_port_history(machine_id: int, port_index: str, range: str = "-24h"):

    """单台 SNMP 设备某端口进出速率历史曲线"""

    return metrics_service.query_port_history(machine_id, port_index, range)





@router.get("/device/{machine_id}/port-errors-history", dependencies=[Depends(require_any_perm("machines", "monitor"))])

async def device_port_errors_history(machine_id: int, port_index: str, range: str = "-24h"):

    """单台 SNMP 设备某端口错包/丢包历史曲线（排障核心）"""

    return metrics_service.query_port_errors_history(machine_id, port_index, range)





@router.get("/device/{machine_id}/vm-history", dependencies=[Depends(require_any_perm("machines", "monitor"))])

async def device_vm_history(

    machine_id: int,

    metric: str = Query("cpu_percent", description="指标: cpu_percent / memory_percent / disk_bytes"),

    range: str = Query("-24h", description="时间范围"),

    db: Session = Depends(get_db),

):

    """单台 PVE 虚拟机的指标历史曲线（vm_metrics）。machine_id 为 VM 子设备 id，

    后端据其 pve_vmid 反查 tag 后查询。"""

    vm = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()

    vmid = vm.pve_vmid if vm else None

    return metrics_service.query_vm_history(machine_id, vmid, metric, range)





@router.get("/collector-health", dependencies=[Depends(require_any_perm("machines", "monitor"))])

async def collector_health():

    """采集器自身健康快照（内存）：各采集器的设备数/成功/失败/耗时等（P2-10）"""

    from services.collector import COLLECTOR_STATS

    return COLLECTOR_STATS





@router.get("/undiscovered", dependencies=[Depends(require_any_perm("machines", "monitor"))])

async def undiscovered_devices(db: Session = Depends(get_db)):

    """基于已采集的 ARP 表，识别被交换机学习到、但未在 machine_info 纳管的 IP（P2-12）。



    运维可直接据此把下挂设备批量纳入监控，无需手动梳理网段。

    """

    from services.collector import PORT_IPMAC_CACHE

    known_ips = {m.ip for m in db.query(MachineInfo).all()}

    out = []

    for mid, ipmac in PORT_IPMAC_CACHE.items():

        dev = db.query(MachineInfo).filter(MachineInfo.id == mid).first()

        dev_name = dev.name if dev else str(mid)

        for ifidx, entries in ipmac.items():

            for e in entries:

                ip = e.get("ip")

                if ip and ip not in known_ips and not ip.startswith("127.") and ip != "0.0.0.0":

                    out.append({"ip": ip, "mac": e.get("mac", ""), "via_device_id": mid,

                                "via_device": dev_name, "via_port_index": ifidx})

    return {"undiscovered": out}





@router.get("/range/{machine_id}", dependencies=[Depends(require_any_perm("machines", "monitor"))])

def get_range(machine_id: int, start: str = Query("-1h"), end: str = Query("now")):

    """获取时间段内所有指标"""

    return metrics_service.query_range(machine_id, start, end)





@router.get("/dashboard", dependencies=[Depends(get_current_user)])  # 全局组件依赖：保持任意登录可读

@ttl_cache(ttl=15)

def get_dashboard(db: Session = Depends(get_db)):

    """首页大屏统计"""

    machines = db.query(MachineInfo).all()

    stats = DashboardStats()

    stats.total_machines = len(machines)



    for m in machines:

        if m.device_type in ("physical", "pve"):

            stats.physical_count += 1

        else:

            stats.vm_count += 1

        if m.online_status == "online":

            stats.online_count += 1

        else:

            stats.offline_count += 1



    stats.today_alerts = alert_service.get_today_alert_count(db)

    today = datetime.now().strftime("%Y-%m-%d")

    stats.alert_machines = db.query(AlertLog.machine_id).filter(

        AlertLog.created_at >= today

    ).distinct().count()



    # 获取所有设备最新指标均值

    all_latest = metrics_service.query_all_latest()

    cpu_vals, mem_vals, disk_vals = [], [], []

    for mid, fields in all_latest.items():

        if "cpu_percent" in fields:

            cpu_vals.append(fields["cpu_percent"])

        if "memory_percent" in fields:

            mem_vals.append(fields["memory_percent"])

        if "disk_percent" in fields:

            disk_vals.append(fields["disk_percent"])



    stats.avg_cpu = round(sum(cpu_vals) / len(cpu_vals), 1) if cpu_vals else 0

    stats.avg_memory = round(sum(mem_vals) / len(mem_vals), 1) if mem_vals else 0

    stats.avg_disk = round(sum(disk_vals) / len(disk_vals), 1) if disk_vals else 0



    return {

        "stats": stats.model_dump(),

        "machines_latest": all_latest,

    }


# ══ MONITOR-SCREEN-20261111：免登录只读监控大屏 ═════════════════════════════
def _screen_mask_ip(value) -> str:
    """大屏只隐藏地址末段；设备识别仍保留名称。"""
    text = str(value or "")
    parts = text.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return ".".join(parts[:3]) + ".*"
    if ":" in text:
        return text.rsplit(":", 1)[0] + ":*"
    return "已隐藏" if text else ""


def _screen_mask_text(value) -> str:
    text = str(value or "")
    return re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
                  lambda m: _screen_mask_ip(m.group(0)), text)


def _screen_num(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


@public_router.get("/screen")
@ttl_cache(ttl=15)
def public_screen(db: Session = Depends(get_db)):
    """监控大屏一次性聚合：只读、脱敏、无凭据/SSH/AI 分析等敏感字段。

    前端每次刷新只请求本接口；趋势由 InfluxDB 单查询聚合，避免 N+1。
    SLA 与磁盘预测继续复用 advanced_routes 的 60s/300s 缓存。
    """
    from routers.advanced_routes import get_sla, all_disk_predictions

    dashboard = get_dashboard(db=db)
    stats = dashboard.get("stats", {})
    latest = dashboard.get("machines_latest", {}) or {}
    machines = db.query(MachineInfo).order_by(MachineInfo.id.asc()).all()
    machine_names = {m.id: _screen_mask_text(m.name or f"设备 {m.id}") for m in machines}

    health = []
    for m in machines:
        values = latest.get(str(m.id), {}) or {}
        cpu = _screen_num(values.get("cpu_percent"))
        memory = _screen_num(values.get("memory_percent"))
        disk = _screen_num(values.get("disk_percent"))
        load = _screen_num(values.get("load_1m"))
        if not m.monitor_enabled:
            level = "disabled"
        elif m.maintenance_mode:
            level = "maintenance"
        elif m.online_status != "online":
            level = "offline"
        elif any(v is not None and v >= 95 for v in (cpu, memory, disk)):
            level = "critical"
        elif ((cpu is not None and cpu >= (m.cpu_threshold or 90)) or
              (memory is not None and memory >= (m.memory_threshold or 90)) or
              (disk is not None and disk >= (m.disk_threshold or 85))):
            level = "warning"
        else:
            level = "normal"
        name = machine_names.get(m.id, f"设备 {m.id}")
        health.append({
            "id": m.id,
            "name": name,
            "ip": _screen_mask_ip(m.ip),
            "device_type": m.device_type or "",
            "group_name": m.group_name or "default",
            "online_status": m.online_status or "unknown",
            "monitor_enabled": bool(m.monitor_enabled),
            "health": level,
            "metrics": {"cpu": cpu, "memory": memory, "disk": disk, "load": load},
        })

    unresolved = db.query(AlertLog).filter(AlertLog.status == "unresolved")
    stats["unresolved_alerts"] = unresolved.count()
    stats["monitored_count"] = sum(1 for m in machines if m.monitor_enabled)
    alerts = []
    for a in unresolved.order_by(AlertLog.created_at.desc()).limit(20).all():
        name = machine_names.get(a.machine_id, f"设备 {a.machine_id}")
        alerts.append({
            "id": a.id,
            "machine_id": a.machine_id,
            "machine_name": name,
            "level": a.alert_level or "warning",
            "type": a.alert_type or "",
            "message": _screen_mask_text(a.message),
            "status": a.status or "unresolved",
            "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else "",
        })

    try:
        sla = get_sla(7, db).get("data", {})
        stats["avg_sla"] = (sla.get("summary", {}) or {}).get("avg_sla", 0)
    except Exception as e:
        logger.warning(f"screen SLA failed: {e}")
        stats["avg_sla"] = 0

    capacity = {"summary": {}, "items": []}
    try:
        disk_data = all_disk_predictions(db).get("data", {})
        risk = [p for p in (disk_data.get("predictions", []) or [])
                if p.get("status") in ("critical", "warning", "notice")]
        risk.sort(key=lambda p: (p.get("days_until_full") is None,
                                 p.get("days_until_full") if p.get("days_until_full") is not None else 999999))
        capacity["summary"] = disk_data.get("summary", {}) or {}
        capacity["items"] = [{
            "machine_id": p.get("machine_id"),
            "machine_name": _screen_mask_text(p.get("machine_name") or machine_names.get(p.get("machine_id"), "")),
            "current_disk_pct": _screen_num(p.get("current_disk_pct")),
            "days_until_full": p.get("days_until_full"),
            "daily_growth_pct": p.get("daily_growth_pct"),
            "status": p.get("status") or "",
            "prediction": p.get("prediction") or "",
        } for p in risk[:8]]
    except Exception as e:
        logger.warning(f"screen disk prediction failed: {e}")

    return {
        "code": 0,
        "data": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stats": stats,
            "machines": health,
            "alerts": alerts,
            "capacity": capacity,
            "trend": metrics_service.query_fleet_trend("-24h", "15m"),
        },
    }

