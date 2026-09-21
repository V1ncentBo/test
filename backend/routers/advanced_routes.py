"""
高级功能路由 — 维护窗口 / 拓扑 / SLA / 磁盘预测 / 告警阈值
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timedelta
from typing import Optional
import logging
from pydantic import BaseModel

from models.database import get_db, MachineInfo

logger = logging.getLogger("advanced")
from routers.auth import get_current_user, require_any_perm, require_admin
router = APIRouter(prefix="/api/advanced", tags=["高级功能"], dependencies=[Depends(get_current_user)])

BJ_TZ = __import__('zoneinfo', fromlist=['ZoneInfo']).ZoneInfo("Asia/Shanghai")

import time as _time

# ── 轻量缓存（key 不含 db session 对象，避免 ttl_cache 装饰器因 session repr 每次 miss）──
_SUMMARY_CACHE = {}   # {days: (ts, result)}
_SLA_CACHE = {}       # {days: (ts, result)}
_DISK_CACHE = {"ts": 0.0, "data": None}
_SUMMARY_TTL = 60     # dashboard-summary 首页聚合：60s 内重复刷新直接命中
_SLA_TTL = 60
_DISK_TTL = 300       # 磁盘耗尽预测变化慢，可缓存更久


# ═══════════════════════════════════════════════════════════
#  1. 维护窗口
# ═══════════════════════════════════════════════════════════

@router.post("/maintenance/{machine_id}")
def set_maintenance(
    machine_id: int,
    duration_minutes: int = Query(30, ge=1, le=1440, description="静默时长(分钟)"),
    db: Session = Depends(get_db),
    _=Depends(require_any_perm("machines")),
):
    """设置设备维护静默期，期间不触发告警。

    PERM-20260917：维护窗口会**静默该设备的全部告警**，属高影响写操作，
    必须纳入权限体系（此前仅需登录，任意用户可静默任意设备 → 越权降噪）。
    """
    machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="设备不存在")

    until = datetime.now() + timedelta(minutes=duration_minutes)
    db.execute(text(
        "UPDATE machine_info SET maintenance_mode=1, maintenance_until=:until WHERE id=:id"
    ), {"until": until, "id": machine_id})
    db.commit()

    return {
        "ok": True,
        "machine_id": machine_id,
        "maintenance_until": until.isoformat(),
        "message": f"已设置维护静默，{duration_minutes}分钟后自动恢复",
    }


@router.delete("/maintenance/{machine_id}")
def clear_maintenance(machine_id: int, db: Session = Depends(get_db),
                      _=Depends(require_any_perm("machines"))):
    """手动解除维护静默（PERM-20260917：同 set_maintenance，需设备管理权限）"""
    db.execute(text(
        "UPDATE machine_info SET maintenance_mode=0, maintenance_until=NULL WHERE id=:id"
    ), {"id": machine_id})
    db.commit()
    return {"ok": True, "message": "维护静默已解除"}


@router.get("/maintenance/list")
def list_maintenance(db: Session = Depends(get_db)):
    """列出当前处于维护模式的设备"""
    rows = db.execute(text(
        "SELECT id, name, ip, maintenance_mode, maintenance_until FROM machine_info WHERE maintenance_mode=1"
    )).mappings().fetchall()

    items = []
    for r in rows:
        until = r["maintenance_until"]
        remaining = ""
        if until:
            delta = until - datetime.now()
            mins = int(delta.total_seconds() / 60)
            remaining = f"剩余 {mins} 分钟" if mins > 0 else "即将到期"
        items.append({
            "id": r["id"],
            "name": r["name"],
            "ip": r["ip"],
            "maintenance_until": until.isoformat() if until else None,
            "remaining": remaining,
        })
    return {"code": 0, "data": items}


@router.get("/maintenance/map")
def maintenance_map(db: Session = Depends(get_db)):
    """返回 {machine_id: {until, remaining_min, active}} 映射（MAINT-20260917）。

    供前端一次性拿到全部维护态，用于设备列表/详情页打「维护中」徽章、
    并对其余设备渲染「进入维护」按钮 —— 避免前端 N 次轮询。
    `active` 由服务端按时钟判定，前端不必再算时间（避免客户端时区/时钟漂移）。
    """
    rows = db.execute(text(
        "SELECT id, maintenance_mode, maintenance_until FROM machine_info"
    )).mappings().fetchall()
    now = datetime.now()
    out = {}
    for r in rows:
        mode = int(r["maintenance_mode"] or 0)
        until = r["maintenance_until"]
        active = bool(mode) and (until is None or now < until)
        if not mode:
            continue
        rem = None
        if until is not None:
            rem = max(0, int((until - now).total_seconds() // 60))
        out[str(r["id"])] = {
            "active": active,
            "until": until.isoformat() if until else None,
            "remaining_min": rem,
        }
    return {"code": 0, "data": out}


# ═══════════════════════════════════════════════════════════
#  2. 设备拓扑
# ═══════════════════════════════════════════════════════════

@router.get("/topology")
def get_topology(db: Session = Depends(get_db)):
    """获取设备拓扑树 (物理机 → 虚拟机)"""
    machines = db.query(MachineInfo).all()

    # 构建树结构
    nodes = {}
    roots = []

    for m in machines:
        nodes[m.id] = {
            "id": m.id,
            "name": m.name,
            "ip": m.ip,
            "parent_id": m.parent_id,
            "device_type": m.device_type,
            "online_status": m.online_status,
            "group_name": m.group_name,
            "cpu": 0, "memory": 0, "disk": 0,
            "children": [],
        }

    # 获取最新指标
    try:
        from services.collector import metrics_service
        latest_all = metrics_service.query_all_latest()
        for mid, metrics in latest_all.items():
            if mid in nodes:
                nodes[mid]["cpu"] = round(metrics.get("cpu_percent", 0), 1)
                nodes[mid]["memory"] = round(metrics.get("memory_percent", 0), 1)
                nodes[mid]["disk"] = round(metrics.get("disk_percent", 0), 1)
    except Exception:
        pass

    # 建立父子关系
    for m in machines:
        if m.parent_id and m.parent_id in nodes:
            nodes[m.parent_id]["children"].append(nodes[m.id])
        elif not m.parent_id:
            roots.append(nodes[m.id])

    return {"code": 0, "data": {"roots": roots, "total": len(machines)}}


# ═══════════════════════════════════════════════════════════
#  2b. 拓扑连线（拖拽调整父子关系）
# ═══════════════════════════════════════════════════════════
#
#  本视图是「宿主机 → 虚拟机」两级树，前端布局只渲染两级；
#  若放任出现三级，孙节点会「被统计到但画不出来」（_all 里存在、画布上没有），
#  所以这里把「两级」作为硬约束在服务端强制执行。
#
#  为此还需要防环：A.parent = B 且 B 是 A 的后代时，A 会从根集合里消失、
#  整棵子树在拓扑里「凭空不见」（前端只从 roots 渲染）。PUT /api/machines 也走同一校验。

def _would_cycle(child_id: int, parent_id: int, db: Session) -> bool:
    """把 child 挂到 parent 下是否会形成环（含自环）。

    做法：从 parent 沿 parent_id 向上爬，若途中遇到 child → 成环。
    爬升时带 visited 集合兜底：即使库里已有历史脏环也不会死循环。
    """
    if parent_id == child_id:
        return True
    seen = set()
    cur = parent_id
    while cur is not None and cur not in seen:
        if cur == child_id:
            return True
        seen.add(cur)
        row = db.query(MachineInfo.parent_id).filter(MachineInfo.id == cur).first()
        cur = row[0] if row else None
    return False


class TopologyLink(BaseModel):
    """拖拽连线请求体。

    parent_id 为 None 表示「解除父子关系」，即把该设备提升为独立根节点。
    """
    child_id: int
    parent_id: Optional[int] = None


@router.post("/topology/link", summary="调整拓扑父子关系（拖拽连线）")
def link_topology(
    body: TopologyLink,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """把 child_id 挂到 parent_id 下；parent_id=None 表示解除父级。

    校验（全部在服务端强制执行，前端只做预判提示）：
      1. child 必须存在
      2. parent_id 非空时：parent 必须存在
      3. 不能挂到自身（自环）
      4. 不能挂到自己的后代（深环）
      5. child 不能已有子节点 —— 否则会出现三级（孙节点渲染不出来）
      6. parent 必须当前是根节点（parent_id 为空）—— 同样为了锁死两级

    返回 changed=False 表示「本来就是这种关系」，前端提示但不算失败。
    """
    child = db.query(MachineInfo).filter(MachineInfo.id == body.child_id).first()
    if not child:
        raise HTTPException(status_code=404, detail="目标设备不存在（可能已被删除）")

    parent = None
    if body.parent_id is not None:
        parent = db.query(MachineInfo).filter(MachineInfo.id == body.parent_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="父设备不存在（可能已被删除）")

    # 自环
    if body.parent_id is not None and body.parent_id == body.child_id:
        raise HTTPException(status_code=400, detail="不能把设备设为它自己的子节点")

    # 深环
    if body.parent_id is not None and _would_cycle(body.child_id, body.parent_id, db):
        raise HTTPException(
            status_code=400,
            detail="会形成循环依赖：%s 是 %s 的下级，不能再反过来挂回去"
                   % (parent.name if parent else body.parent_id, child.name),
        )

    if body.parent_id is not None:
        # 规则 5：child 不能有子节点
        n_kids = db.query(MachineInfo).filter(MachineInfo.parent_id == body.child_id).count()
        if n_kids:
            raise HTTPException(
                status_code=400,
                detail="“%s”自身有 %d 个子节点，不能再挂到其它设备下（本视图为两级：宿主机 → 虚拟机）"
                       % (child.name, n_kids),
            )

        # 规则 6：parent 必须是根节点
        if parent.parent_id is not None:
            raise HTTPException(
                status_code=400,
                detail="“%s”已经有父设备了，不能再作为宿主机（本视图为两级：宿主机 → 虚拟机）"
                       % parent.name,
            )

    changed = child.parent_id != body.parent_id
    if changed:
        child.parent_id = body.parent_id
        child.updated_at = datetime.now()
        db.commit()

    return {
        "code": 0,
        "data": {
            "changed": changed,
            "child_id": child.id,
            "child_name": child.name,
            "parent_id": body.parent_id,
            "parent_name": parent.name if parent else None,
        },
    }


# ═══════════════════════════════════════════════════════════
#  3. SLA 可用性
# ═══════════════════════════════════════════════════════════

@router.get("/sla")
def get_sla(days: int = Query(7, ge=1, le=90, description="统计天数"), db: Session = Depends(get_db)):
    """计算各设备 SLA 可用率（批量查询：1 次 InfluxDB 查询取代逐台 N 次）"""
    cached = _SLA_CACHE.get(days)
    if cached and _time.time() - cached[0] < _SLA_TTL:
        return cached[1]

    machines = db.query(MachineInfo).filter(MachineInfo.monitor_enabled != False).all()  # 排除未监控设备
    counts = _sla_counts_batch(days)
    expected_points = days * 24 * 60

    # 早期预警：一次 query_all_latest 取代逐台 query_latest
    try:
        from services.collector import metrics_service
        latest_all = metrics_service.query_all_latest()
    except Exception:
        latest_all = {}

    results = []
    total_early_warnings = 0
    for m in machines:
        actual = counts.get(str(m.id), 0)
        sla_pct = min(100.0, (actual / expected_points) * 100.0) if expected_points else 100.0
        latest = latest_all.get(str(m.id), {}) or {}
        warn = bool(latest) and (latest.get("disk_percent", 0) >= 85 or latest.get("memory_percent", 0) >= 90)
        if warn:
            total_early_warnings += 1
        results.append({
            "machine_id": m.id, "name": m.name, "ip": m.ip,
            "device_type": m.device_type, "online_status": m.online_status,
            "sla": round(sla_pct, 2), "days": days, "early_warning": warn,
        })

    results.sort(key=lambda x: x["sla"])
    avg_sla = round(sum(r["sla"] for r in results) / len(results), 2) if results else 100
    healthy = sum(1 for r in results if r["sla"] >= 99.9)
    warning = sum(1 for r in results if 99 <= r["sla"] < 99.9)
    out = {
        "code": 0,
        "data": {
            "summary": {
                "avg_sla": avg_sla,
                "healthy_count": healthy,
                "warning_count": warning,
                "target_days": days,
                "early_warnings": total_early_warnings,
            },
            "machines": results,
        },
    }
    _SLA_CACHE[days] = (_time.time(), out)
    return out


def _sla_counts_batch(days: int) -> dict:
    """一次 InfluxDB 查询，按 machine_id 分组统计各设备 days 天内的 cpu_percent 数据点数。
    取代原逐台 _calc_sla：N 台设备 N 次往返 + 每次新建连接 → 1 次查询复用全局连接。"""
    try:
        from services.collector import metrics_service
        from config import INFLUXDB_ORG, INFLUXDB_BUCKET

        now = datetime.now()
        start = now - timedelta(days=days)
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: {start.strftime("%Y-%m-%dT%H:%M:%SZ")}, stop: {now.strftime("%Y-%m-%dT%H:%M:%SZ")})
          |> filter(fn: (r) => r["_field"] == "cpu_percent")
          |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
          |> count()
        '''
        result = metrics_service.query_api.query(query, org=INFLUXDB_ORG)
        counts = {}
        for table in result:
            for record in table.records:
                mid = record.values.get("machine_id")
                if mid is not None:
                    counts[str(mid)] = record.get_value() or 0
        return counts
    except Exception as e:
        logger.warning(f"SLA batch count failed: {e}")
        return {}


# ═══════════════════════════════════════════════════════════
#  4. 磁盘空间预测
# ═══════════════════════════════════════════════════════════

@router.get("/disk-prediction/{machine_id}")
def disk_prediction(machine_id: int, db: Session = Depends(get_db)):
    """基于历史数据预测磁盘耗尽时间"""
    machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="设备不存在")

    try:
        from services.collector import metrics_service
        from config import INFLUXDB_ORG, INFLUXDB_BUCKET

        now = datetime.now()
        start = now - timedelta(days=30)

        query_api = metrics_service.query_api

        # 获取最近30天的磁盘使用率
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: {start.strftime("%Y-%m-%dT%H:%M:%SZ")})
          |> filter(fn: (r) => r["machine_id"] == "{machine_id}")
          |> filter(fn: (r) => r["_field"] == "disk_percent")
          |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
        '''
        result = query_api.query(query, org=INFLUXDB_ORG)

        # 提取数据点
        points = []
        for table in result:
            for record in table.records:
                t = record.get_time()
                v = record.get_value()
                if v is not None:
                    points.append((t.timestamp(), v))

        if len(points) < 10:
            # 如果数据不足30天，尝试7天
            start7 = now - timedelta(days=7)
            query7 = f'''
            from(bucket: "{INFLUXDB_BUCKET}")
              |> range(start: {start7.strftime("%Y-%m-%dT%H:%M:%SZ")})
              |> filter(fn: (r) => r["machine_id"] == "{machine_id}")
              |> filter(fn: (r) => r["_field"] == "disk_percent")
              |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
            '''
            result7 = query_api.query(query7, org=INFLUXDB_ORG)
            for table in result7:
                for record in table.records:
                    t = record.get_time()
                    v = record.get_value()
                    if v is not None:
                        points.append((t.timestamp(), v))

        if len(points) < 5:
            return {
                "code": 0,
                "data": {
                    "machine_name": machine.name,
                    "current_disk_pct": 0,
                    "days_until_full": None,
                    "prediction": "数据不足，需要更多历史数据",
                    "status": "insufficient",
                },
            }

        # 线性回归: y = mx + b
        # x 是时间戳(天)，y 是磁盘使用率
        n = len(points)
        base_ts = points[0][0]
        xs = [(p[0] - base_ts) / 86400 for p in points]  # 转天数
        ys = [p[1] for p in points]

        sum_x = sum(xs)
        sum_y = sum(ys)
        sum_xy = sum(x * y for x, y in zip(xs, ys))
        sum_x2 = sum(x * x for x in xs)

        denominator = n * sum_x2 - sum_x * sum_x
        if abs(denominator) < 1e-10:
            slope = 0
        else:
            slope = (n * sum_xy - sum_x * sum_y) / denominator

        intercept = (sum_y - slope * sum_x) / n

        current_pct = ys[-1]
        current_day = xs[-1]

        # 预测磁盘达到 100% 的天数
        if slope <= 0:
            prediction = "磁盘使用率趋势稳定或下降，暂无风险"
            days_left = None
            status = "healthy"
        else:
            # 100 = slope * (current_day + days_left) + intercept
            days_left = (100 - intercept) / slope - current_day
            days_left = round(max(0, days_left), 1)
            if days_left <= 7:
                status = "critical"
                prediction = f"磁盘预计 {days_left} 天后耗尽，请立即清理或扩容！"
            elif days_left <= 30:
                status = "warning"
                prediction = f"磁盘预计 {days_left} 天后耗尽，建议提前规划"
            elif days_left <= 90:
                status = "notice"
                prediction = f"磁盘预计 {days_left} 天后耗尽，请关注"
            else:
                status = "healthy"
                prediction = f"磁盘使用趋势平缓，预计 {int(days_left)} 天后达到100%"

        return {
            "code": 0,
            "data": {
                "machine_name": machine.name,
                "current_disk_pct": round(current_pct, 1),
                "daily_growth_pct": round(slope, 3) if slope > 0 else 0,
                "days_until_full": days_left,
                "prediction": prediction,
                "status": status,
                "trend": "up" if slope > 0 else "down" if slope < 0 else "stable",
            },
        }

    except Exception as e:
        logger.error(f"Disk prediction failed: {e}")
        return {
            "code": -1,
            "data": {
                "machine_name": machine.name,
                "prediction": f"计算失败: {str(e)}",
                "status": "error",
            },
        }


@router.get("/disk-predictions")
def all_disk_predictions(db: Session = Depends(get_db)):
    """获取所有设备的磁盘预测摘要（结果缓存 300s，磁盘趋势变化慢）"""
    if _DISK_CACHE["data"] is not None and _time.time() - _DISK_CACHE["ts"] < _DISK_TTL:
        return _DISK_CACHE["data"]

    machines = db.query(MachineInfo).all()
    results = []
    warnings = 0
    criticals = 0

    for m in machines:
        try:
            pred = disk_prediction(m.id, db)
            if pred["code"] == 0:
                d = pred["data"]
                d["machine_id"] = m.id
                results.append(d)
                if d["status"] == "warning":
                    warnings += 1
                elif d["status"] == "critical":
                    criticals += 1
        except Exception:
            pass

    out = {
        "code": 0,
        "data": {
            "summary": {"total": len(machines), "warnings": warnings, "critical": criticals},
            "predictions": results,
        },
    }
    _DISK_CACHE["data"] = out
    _DISK_CACHE["ts"] = _time.time()
    return out


# ═══════════════════════════════════════════════════════════
#  5. 告警持续时间阈值
# ═══════════════════════════════════════════════════════════

@router.put("/alert-duration/{machine_id}")
def set_alert_duration(
    machine_id: int,
    duration: int = Query(0, ge=0, le=600, description="告警持续时间阈值(秒), 0=立即触发"),
    db: Session = Depends(get_db),
):
    """设置设备告警持续时间阈值"""
    machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="设备不存在")

    db.execute(text(
        "UPDATE machine_info SET alert_duration=:d WHERE id=:id"
    ), {"d": duration, "id": machine_id})
    db.commit()

    return {
        "ok": True,
        "machine_id": machine_id,
        "alert_duration": duration,
        "message": f"告警持续时间阈值已设为 {duration}秒" if duration > 0 else "已恢复为即时告警",
    }


@router.get("/dashboard-summary", dependencies=[Depends(require_any_perm("dashboard"))])
def dashboard_summary(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db)):
    """Dashboard 综合摘要：SLA + 磁盘预测 + 维护状态（结果缓存 60s）"""
    cached = _SUMMARY_CACHE.get(days)
    if cached and _time.time() - cached[0] < _SUMMARY_TTL:
        return cached[1]

    # SLA
    sla_data = get_sla(days, db)
    # 磁盘预测
    disk_data = all_disk_predictions(db)
    # 维护状态
    maint = list_maintenance(db)

    out = {
        "code": 0,
        "data": {
            "sla": sla_data.get("data", {}),
            "disk": disk_data.get("data", {}),
            "maintenance": maint.get("data", []),
        },
    }
    _SUMMARY_CACHE[days] = (_time.time(), out)
    return out
