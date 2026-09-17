"""
Batch 1 高级功能 — 分组聚合 / SLA趋势 / 同环比 / 指标基线 / 端口探测
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import logging, socket, json

from models.database import get_db, MachineInfo
from routers.auth import get_current_user
from services.cache import ttl_cache

logger = logging.getLogger("batch1")
router = APIRouter(prefix="/api/advanced", tags=["Batch1-高级功能"], dependencies=[Depends(get_current_user)])
BJT = __import__('zoneinfo', fromlist=['ZoneInfo']).ZoneInfo("Asia/Shanghai")


# ═══════════════════════════════════════════════════════════
#  6. 分组聚合统计
# ═══════════════════════════════════════════════════════════

@router.get("/group-stats")

# PERF-20260915：本端点每次都会跑 query_all_latest()（2 次 Flux，实测 ~150ms），
# 实测响应 0.17~0.20s，且没有任何缓存。分组聚合变化很慢，20s 内复用即可。
@ttl_cache(ttl=20)

def group_stats(db: Session = Depends(get_db)):
    """按分组统计平均 CPU/内存/磁盘"""
    machines = db.query(MachineInfo).all()
    groups = {}
    for m in machines:
        g = m.group_name or "default"
        if g not in groups:
            groups[g] = {"name": g, "total": 0, "online": 0, "cpu": [], "mem": [], "disk": []}
        groups[g]["total"] += 1
        if m.online_status == "online":
            groups[g]["online"] += 1

    # Get latest metrics
    try:
        from services.collector import metrics_service
        latest_all = metrics_service.query_all_latest()
        for mid, metrics in latest_all.items():
            m = next((x for x in machines if x.id == mid), None)
            if m:
                g = m.group_name or "default"
                if g in groups:
                    groups[g]["cpu"].append(metrics.get("cpu_percent", 0))
                    groups[g]["mem"].append(metrics.get("memory_percent", 0))
                    groups[g]["disk"].append(metrics.get("disk_percent", 0))
    except Exception:
        pass

    result = []
    for g in sorted(groups.values(), key=lambda x: x["name"]):
        result.append({
            "name": g["name"],
            "total": g["total"],
            "online": g["online"],
            "avg_cpu": round(sum(g["cpu"]) / max(len(g["cpu"]), 1), 1),
            "avg_mem": round(sum(g["mem"]) / max(len(g["mem"]), 1), 1),
            "avg_disk": round(sum(g["disk"]) / max(len(g["disk"]), 1), 1),
            "device_count": g["total"],
        })

    return {"code": 0, "data": result}


# ═══════════════════════════════════════════════════════════
#  7. SLA 趋势数据
# ═══════════════════════════════════════════════════════════

@router.get("/sla-trend")
def sla_trend(days: int = Query(30, ge=7, le=90), db: Session = Depends(get_db)):
    """返回 SLA 按天趋势数据"""
    machines = db.query(MachineInfo).all()
    now = datetime.now()

    try:
        from influxdb_client import InfluxDBClient
        from config import INFLUXDB_URL, INFLUXDB_TOKEN, INFLUXDB_ORG, INFLUXDB_BUCKET
        client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
        query_api = client.query_api()
    except Exception:
        return {"code": -1, "data": {"error": "InfluxDB unavailable"}}

    # Aggregate by day
    daily_points = {}
    for d in range(days):
        day = (now - timedelta(days=d)).strftime("%Y-%m-%d")
        daily_points[day] = {"total": 0, "online_minutes": 0}

    for m in machines:
        for d in range(days):
            day = (now - timedelta(days=d))
            day_start = day.replace(hour=0, minute=0, second=0)
            day_end = day.replace(hour=23, minute=59, second=59)
            day_str = day.strftime("%Y-%m-%d")

            try:
                query = f'''
                from(bucket: "{INFLUXDB_BUCKET}")
                  |> range(start: {day_start.strftime("%Y-%m-%dT%H:%M:%SZ")}, stop: {day_end.strftime("%Y-%m-%dT%H:%M:%SZ")})
                  |> filter(fn: (r) => r["machine_id"] == "{m.id}")
                  |> filter(fn: (r) => r["_field"] == "cpu_percent")
                  |> aggregateWindow(every: 10m, fn: mean, createEmpty: false)
                  |> count()
                '''
                result = query_api.query(query, org=INFLUXDB_ORG)
                count = 0
                for table in result:
                    for record in table.records:
                        count = record.get_value() or 0
                        break
                expected = 144  # 24h * 6 per hour (10min intervals)
                sla_pct = min(100, count / max(expected, 1) * 100)
                daily_points[day_str]["total"] += sla_pct
                daily_points[day_str]["online_minutes"] += 1
            except Exception as e:
                logger.warning(f"Metric fetch failed: {e}")

    trend = []
    for day_str in sorted(daily_points.keys()):
        dp = daily_points[day_str]
        avg = round(dp["total"] / max(dp["online_minutes"], 1), 2) if dp["online_minutes"] > 0 else 100
        trend.append({"date": day_str, "sla": avg})

    return {"code": 0, "data": {"days": days, "trend": trend}}


# ═══════════════════════════════════════════════════════════
#  8. 历史同环比
# ═══════════════════════════════════════════════════════════

@router.get("/compare/{machine_id}")
def compare_metrics(
    machine_id: int,
    metric: str = Query("cpu_percent", description="指标名"),
    period: str = Query("24h", description="对比周期: 24h/7d/30d"),
    db: Session = Depends(get_db),
):
    """对比当前周期与上个周期的指标"""
    machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="设备不存在")

    now = datetime.now()
    if period == "24h":
        dur = timedelta(hours=24)
        interval = "1h"
    elif period == "7d":
        dur = timedelta(days=7)
        interval = "6h"
    else:
        dur = timedelta(days=30)
        interval = "1d"

    current_start = now - dur
    previous_start = current_start - dur

    try:
        from influxdb_client import InfluxDBClient
        from config import INFLUXDB_URL, INFLUXDB_TOKEN, INFLUXDB_ORG, INFLUXDB_BUCKET
        client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
        query_api = client.query_api()

        def fetch_series(start, end):
            query = f'''
            from(bucket: "{INFLUXDB_BUCKET}")
              |> range(start: {start.strftime("%Y-%m-%dT%H:%M:%SZ")}, stop: {end.strftime("%Y-%m-%dT%H:%M:%SZ")})
              |> filter(fn: (r) => r["machine_id"] == "{machine_id}")
              |> filter(fn: (r) => r["_field"] == "{metric}")
              |> aggregateWindow(every: {interval}, fn: mean, createEmpty: false)
            '''
            points = []
            result = query_api.query(query, org=INFLUXDB_ORG)
            for table in result:
                for record in table.records:
                    t = record.get_time()
                    v = record.get_value()
                    if v is not None:
                        points.append({"time": t.isoformat(), "value": round(v, 2)})
            return points

        current = fetch_series(current_start, now)
        previous = fetch_series(previous_start, current_start)

        # Calculate averages
        avg_current = round(sum(p["value"] for p in current) / max(len(current), 1), 2)
        avg_previous = round(sum(p["value"] for p in previous) / max(len(previous), 1), 2)

        change_pct = 0
        if avg_previous > 0:
            change_pct = round((avg_current - avg_previous) / avg_previous * 100, 1)

        return {
            "code": 0,
            "data": {
                "metric": metric,
                "period": period,
                "avg_current": avg_current,
                "avg_previous": avg_previous,
                "change_pct": change_pct,
                "trend": "up" if change_pct > 2 else "down" if change_pct < -2 else "stable",
                "current_series": current,
                "previous_series": previous,
            },
        }
    except Exception as e:
        return {"code": -1, "data": {"error": str(e)}}


# ═══════════════════════════════════════════════════════════
#  9. 指标基线 (供 alert_service 使用)
# ═══════════════════════════════════════════════════════════

# 基线缓存: {machine_id: {metric: {mean, std}}}
_baselines = {}
_baseline_updated = {}

@router.get("/baseline/{machine_id}")
def get_baseline(machine_id: int):
    """获取设备指标基线"""
    key = str(machine_id)
    if key in _baselines:
        return {"code": 0, "data": _baselines[key]}

    try:
        from influxdb_client import InfluxDBClient
        from config import INFLUXDB_URL, INFLUXDB_TOKEN, INFLUXDB_ORG, INFLUXDB_BUCKET
        client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
        query_api = client.query_api()
        now = datetime.now()
        start = now - timedelta(days=14)

        baselines = {}
        for metric in ["cpu_percent", "memory_percent", "disk_percent"]:
            query = f'''
            from(bucket: "{INFLUXDB_BUCKET}")
              |> range(start: {start.strftime("%Y-%m-%dT%H:%M:%SZ")}, stop: {now.strftime("%Y-%m-%dT%H:%M:%SZ")})
              |> filter(fn: (r) => r["machine_id"] == "{machine_id}")
              |> filter(fn: (r) => r["_field"] == "{metric}")
              |> aggregateWindow(every: 10m, fn: mean, createEmpty: false)
            '''
            values = []
            result = query_api.query(query, org=INFLUXDB_ORG)
            for table in result:
                for record in table.records:
                    v = record.get_value()
                    if v is not None: values.append(v)

            if len(values) >= 30:
                mean = sum(values) / len(values)
                variance = sum((v - mean) ** 2 for v in values) / len(values)
                std = variance ** 0.5
                # Anomaly threshold = mean + 3 * std
                baselines[metric] = {
                    "mean": round(mean, 2),
                    "std": round(std, 2),
                    "threshold": round(min(mean + 3 * std, 95), 1),
                    "samples": len(values),
                }
            else:
                baselines[metric] = {"mean": 0, "std": 0, "threshold": 90, "samples": len(values)}

        _baselines[key] = baselines
        _baseline_updated[key] = datetime.now()
        return {"code": 0, "data": baselines}
    except Exception as e:
        return {"code": -1, "data": {"error": str(e)}}


# ═══════════════════════════════════════════════════════════
#  10. TCP 端口探测
# ═══════════════════════════════════════════════════════════

COMMON_PORTS = [
    {"port": 22, "name": "SSH", "icon": "🔐"},
    {"port": 80, "name": "HTTP", "icon": "🌐"},
    {"port": 443, "name": "HTTPS", "icon": "🔒"},
    {"port": 3306, "name": "MySQL", "icon": "🗄"},
    {"port": 8086, "name": "InfluxDB", "icon": "📊"},
    {"port": 6379, "name": "Redis", "icon": "⚡"},
    {"port": 9090, "name": "Prometheus", "icon": "📈"},
    {"port": 3000, "name": "Grafana", "icon": "📉"},
    {"port": 8080, "name": "API", "icon": "🔗"},
    {"port": 8443, "name": "API-SSL", "icon": "🔗"},
]


def check_tcp_port(host: str, port: int, timeout: float = 2.0) -> dict:
    """探测 TCP 端口"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return {"open": result == 0, "latency_ms": None}
    except Exception as e:
        return {"open": False, "error": str(e)[:100]}


@router.get("/port-check/{machine_id}")
def check_ports(
    machine_id: int,
    ports: str = Query("", description="逗号分隔端口列表，空=默认常用端口"),
    db: Session = Depends(get_db),
):
    """探测设备端口"""
    machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="设备不存在")

    target_ports = COMMON_PORTS
    if ports:
        try:
            custom = [int(p.strip()) for p in ports.split(",") if p.strip().isdigit()]
            if custom:
                target_ports = [{"port": p, "name": f"Port-{p}", "icon": "🔌"} for p in custom]
        except Exception as e:
            logger.warning(f"Query failed: {e}")

    results = []
    for p in target_ports:
        status = check_tcp_port(machine.ip, p["port"])
        results.append({
            "port": p["port"],
            "name": p["name"],
            "icon": p["icon"],
            "open": status["open"],
        })

    open_count = sum(1 for r in results if r["open"])
    total = len(results)

    return {
        "code": 0,
        "data": {
            "machine_id": machine_id,
            "machine_name": machine.name,
            "ip": machine.ip,
            "results": results,
            "summary": f"{open_count}/{total} 端口开放",
            "open_count": open_count,
            "total": total,
        },
    }
