"""监控数据 API 路由"""

from fastapi import APIRouter, Depends, HTTPException, Query

from sqlalchemy.orm import Session

from typing import List, Optional

from models.database import get_db, MachineInfo, AlertLog

from routers.auth import get_current_user, require_any_perm

from models.schema import MetricData, MetricQuery, MetricSeries, DashboardStats

from services.collector import metrics_service

from services.cache import ttl_cache

from services.alert_service import alert_service

from services.dingtalk import send_alert as dingtalk_send

from services.email_service import send_alert as email_send

from websocket.manager import ws_manager

from datetime import datetime

import asyncio

import logging

logger = logging.getLogger("metrics")



router = APIRouter(prefix="/api/metrics", tags=["监控数据"], )





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

    except Exception:

        pass

    return {"machine_id": machine_id, "metrics": data}





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

async def snmp_ports_summary(db: Session = Depends(get_db)):

    """所有 SNMP 设备的端口 UP/total 汇总，供设备管理列表「端口状态」列展示"""

    devices = db.query(MachineInfo).filter(

        MachineInfo.device_type == "snmp",

        MachineInfo.monitor_enabled == True,

    ).all()

    out = {}

    for d in devices:

        ports = metrics_service.query_port_latest(d.id)

        total = len(ports)

        up = sum(1 for p in ports.values() if (p.get("ifOperStatus") or 0) == 1)

        down_ports = [p.get("port_name") for p in ports.values()

                      if (p.get("ifOperStatus") or 0) != 1 and p.get("port_name")]

        out[str(d.id)] = {"total": total, "up": up, "down_ports": down_ports}

    return out





@router.get("/device/{machine_id}/ports", dependencies=[Depends(require_any_perm("machines", "monitor"))])

async def device_ports(machine_id: int, db: Session = Depends(get_db)):

    """单台 SNMP 设备端口最新状态表（详情页端口视图用）"""

    device = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()

    if not device:

        raise HTTPException(status_code=404, detail="设备不存在")

    ports = metrics_service.query_port_latest(machine_id)

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

