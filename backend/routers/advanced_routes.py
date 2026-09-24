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

        query_api = metrics_service.query_api

        # 获取最近30天的磁盘使用率
        # ⛔⛔ 必须限定 `_measurement`：同一 machine_id 下 `machine_metrics`（node_exporter /
        #   agent 上报的真实值）与 `vm_metrics`（PVE 侧，disk_percent 恒为 0）**并存**。
        #   `aggregateWindow` 会按 measurement 分表，不限定就会把两套序列一起塞进 points，
        #   变成「真值 / 0」交替（实测 mid=51：machine_metrics n=690 值 48.8~49.2，
        #   vm_metrics n=159 恒 0.0）⇒ 两序列覆盖区间与条数比随时间漂移，回归斜率被彻底污染
        #   （实测虚增出 26.375 / 1038.767 个百分点/天的假 critical）。
        #   口径与 query_latest 一致：machine_metrics 优先，vm_metrics 回退。
        def _disk_points(measurement: str, days: int) -> list:
            q = f'''
            from(bucket: "{INFLUXDB_BUCKET}")
              |> range(start: {(now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")})
              |> filter(fn: (r) => r["_measurement"] == "{measurement}")
              |> filter(fn: (r) => r["machine_id"] == "{machine_id}")
              |> filter(fn: (r) => r["_field"] == "disk_percent")
              |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
            '''
            out = {}
            for table in query_api.query(q, org=INFLUXDB_ORG):
                for record in table.records:
                    v = record.get_value()
                    if v is not None:
                        out[record.get_time().timestamp()] = v
            return out

        # 依次尝试：30 天 → 7 天 → 换 vm_metrics 重来。按时间戳去重，避免区间重叠处重复计点。
        points = []
        for _meas in ("machine_metrics", "vm_metrics"):
            for _days in (30, 7):
                got = _disk_points(_meas, _days)
                if len(got) >= 10:
                    points = sorted(got.items())
                    break
            if points:
                break
        # 全都不足 10 点时，用能取到的最大集合做后续「< 5 点」判定
        if not points:
            merged = {}
            for _meas in ("machine_metrics", "vm_metrics"):
                merged.update(_disk_points(_meas, 30))
            points = sorted(merged.items())

        # ⛔⛔ 阶跃 / 口径变更守卫（STEPGUARD-20260924）────────────────────────
        #   采集口径变更（含本次 disk_percent 挂载点从 /home 纠正为 /）会在序列里
        #   留下一次「垂直线」，而 30 天线性回归会把它摊成一条假的上升趋势：
        #   实测 mid=52(jenkins-1.105) 09-21 01:00~09-24 08:00 恒 11.1%，
        #   09-24 09:00 → 36.2%、09:15 → 52.4%（1 小时内 +41 个百分点），
        #   回归却报「日均增长 1.377%，62.3 天后耗尽」——机器真实状态是「3 天不动」。
        #   ⇒ 只保留**最后一次大幅跳变之后**的样本（跳变点自身保留作新基线），
        #     再由下面的「样本跨度 < 24h」闸把它判成 insufficient，诚实报「暂不外推」。
        #   ⛔ 判据必须是「**电平位移**」而不是「相邻两点差值」：小时均值上，
        #     小容量盘一次正常写盘就能差出 5 个百分点，用逐点差值会把样本砍光、
        #     白白丢掉预测。这里取跳变点前 6 点 / 后 3 点的**中位数**之差，
        #     中位数抗单点尖刺，只认真实台阶（口径变更 / 一次性大写入）。
        STEP_PCT = 5.0        # 电平位移阈值（百分点）
        STEP_BEFORE = 6       # 位移前窗口（小时均值点）
        STEP_AFTER = 3        # 位移后窗口

        def _median(xs):
            s = sorted(xs)
            n = len(s)
            return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0

        step_from = None
        for _i in range(1, len(points)):
            _b = [p[1] for p in points[max(0, _i - STEP_BEFORE):_i]]
            _a = [p[1] for p in points[_i:_i + STEP_AFTER]]
            if _b and _a and abs(_median(_a) - _median(_b)) >= STEP_PCT:
                step_from = _i
        if step_from:
            points = points[step_from:]

        if len(points) < 5:
            # ⛔ 不要写死 current_disk_pct=0：只要还有样本，它就是**实测值**。
            #   写死 0 会让「磁盘 52.4% 的机器在卡片上显示 0%」——正是用户投诉过的观感矛盾。
            return {
                "code": 0,
                "data": {
                    "machine_name": machine.name,
                    "current_disk_pct": round(points[-1][1], 1) if points else 0,
                    "days_until_full": None,
                    "prediction": ("采集口径/数据源刚变更，新基线样本不足，暂不外推"
                                   if step_from else "数据不足，需要更多历史数据"),
                    "status": "insufficient",
                },
            }

        # ⛔ 外推至少需要跨一个完整日周期的观察窗口。
        #   只有几小时样本时（今天新加的机器、或采集口径刚切换），回归会把「阶跃」当增长趋势：
        #   实测 1.195 因 disk_percent 从旧 /home 口径(0.2%)切到 / 口径(8.5%)，
        #   在 ~30 分钟窗口上被外推成 43 个百分点/天 ⇒ 假 critical「2.2 天后耗尽」。
        span_hours = (points[-1][0] - points[0][0]) / 3600
        if span_hours < 24:
            # 命中阶跃守卫时，把「口径刚变更」讲明白，否则用户会把 insufficient 读成「机器异常」
            _why = (f"采集口径/数据源刚变更，新基线仅 {span_hours:.1f} 小时"
                    if step_from else f"历史数据仅覆盖 {span_hours:.1f} 小时")
            return {
                "code": 0,
                "data": {
                    "machine_name": machine.name,
                    "current_disk_pct": round(points[-1][1], 1),
                    "days_until_full": None,
                    "prediction": f"{_why}（不足一天），暂不外推，明日再看",
                    "status": "insufficient",
                    "trend": "stable",
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

        # ── 退化数据清洗（DISKPRED-CLEANUP-20260924）────────────────
        # slope 是「每天增长多少个百分点」。回归斜率在数值噪声下常拿到 1e-9 ~ 1e-4
        # 这种量级，代入 (100-intercept)/slope 会外推出 2.39e16 / 5130745.6 这类
        # 天文数字；同时 round(slope, 3) 会把它抹成 0.0，却仍判 trend="up" 自相矛盾。
        # 口径：日均增长 < 0.01 个百分点 ⇒ 视为无增长；外推 > 3650 天（10 年）⇒ 无风险。
        # 该口径与前端 capacity.html 的 `days_until_full < 3650` 判定保持一致。
        MIN_SLOPE = 0.01
        MAX_DAYS = 3650
        slope_eff = slope if slope > 0 else 0.0

        if slope_eff < MIN_SLOPE:
            # 覆盖 slope <= 0（稳定/下降）与 slope 近零（噪声）两种情形
            days_left = None
            status = "healthy"
            if slope_eff <= 0:
                prediction = "磁盘使用率趋势稳定或下降，暂无风险"
                trend = "down" if slope_eff < 0 else "stable"
            else:
                prediction = "磁盘使用趋势平缓（日均增长不足 0.01%），暂无风险"
                trend = "stable"
        else:
            # ⛔ 外推基线锚在**实测当前值**，不要用回归线截距：
            #     卡片同时展示「当前使用率 / 日均增长 / 剩余天数」，用户会心算
            #     (100 - pct) / growth 交叉验证。锚回归线会出现
            #     「52.4% 且日均 +1.377%」却「还要 62.3 天」这类自相矛盾
            #     （该机器回归线在当下的预报值只有 14.2%，与实测 52.4% 差 38 个百分点）。
            #     锚实测值后，三条信息恒满足 days_left == (100 - current_pct) / slope_eff。
            days_left = (100 - current_pct) / slope
            days_left = round(max(0, days_left), 1)
            trend = "up"
            if days_left <= 7:
                status = "critical"
                prediction = f"磁盘预计 {days_left} 天后耗尽，请立即清理或扩容！"
            elif days_left <= 30:
                status = "warning"
                prediction = f"磁盘预计 {days_left} 天后耗尽，建议提前规划"
            elif days_left <= 90:
                status = "notice"
                prediction = f"磁盘预计 {days_left} 天后耗尽，请关注"
            elif days_left <= MAX_DAYS:
                status = "healthy"
                prediction = f"磁盘使用趋势平缓，预计 {int(days_left)} 天后达到100%"
            else:
                # 外推超过 10 年，无实际意义 ⇒ 当作无风险，且不吐天文数字
                status = "healthy"
                days_left = None
                prediction = "磁盘使用趋势平缓，暂无风险"

        return {
            "code": 0,
            "data": {
                "machine_name": machine.name,
                "current_disk_pct": round(current_pct, 1),
                # slope_eff >= MIN_SLOPE 时 round(slope,3) 必 > 0，与 trend="up" 不再矛盾
                "daily_growth_pct": round(slope_eff, 3) if slope_eff >= MIN_SLOPE else 0,
                "days_until_full": days_left,
                "prediction": prediction,
                "status": status,
                "trend": trend,
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
    notices = 0
    insufficient = 0

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
                elif d["status"] == "notice":
                    notices += 1
                elif d["status"] == "insufficient":
                    insufficient += 1
        except Exception:
            pass

    out = {
        "code": 0,
        "data": {
            "summary": {
                "total": len(machines),
                "warnings": warnings,
                "critical": criticals,
                "notices": notices,
                "insufficient": insufficient,
                # attention = 需要人工关注的设备数（紧急 + 预警 + 关注）
                "attention": criticals + warnings + notices,
            },
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
