"""数据库监控路由 — 只读采集 + 实例管理
端点（挂在 /api/dbs）：
  GET    /api/dbs/instances          读（require_any_perm("dbs")，admin 恒放行）
  POST   /api/dbs/instances          写（require_admin）
  PUT    /api/dbs/instances/{id}     写（require_admin）
  DELETE /api/dbs/instances/{id}     写（require_admin）
  POST   /api/dbs/test               只读连通性探测（require_admin，绝不写目标库）
  GET    /api/dbs/{id}/metrics       读（require_any_perm("dbs")，真实趋势）
"""
import os
import time
import json
import base64
import asyncio
import urllib.request
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models.database import SessionLocal, DbInstance, get_db
from routers.auth import require_admin, require_any_perm, get_current_user
from services.crypto import encrypt_password, decrypt_password
from services.collector import metrics_service


router = APIRouter(
    prefix="/api/dbs",
    tags=["数据库监控"],
    dependencies=[Depends(get_current_user)],
)

# 每种类型用于趋势图的主指标（hero）
HERO_FIELD = {
    "mysql": "connections",
    "mongodb": "connections",
    "postgresql": "qps",
    "clickhouse": "qps",
    "redis": "memory_usage_pct",
    "elasticsearch": "query_latency_ms",
}

# 采集周期内存状态（用于 QPS / 慢查询速率的逐周期差分）
_DB_PREV = {}


# ───────────────────────── 请求模型 ─────────────────────────
class DbInstanceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    type: str = Field(default="mysql")
    host: str = Field(..., min_length=1, max_length=128)
    port: int = Field(default=3306, ge=1, le=65535)
    account: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)
    readonly: bool = True
    enabled: bool = True
    tags: list = Field(default=[])
    extra_params: dict = Field(default={})


class DbInstanceUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    account: Optional[str] = None
    password: Optional[str] = None
    readonly: Optional[bool] = None
    enabled: Optional[bool] = None
    tags: Optional[list] = None
    extra_params: Optional[dict] = None


class DbTestRequest(BaseModel):
    type: str = "mysql"
    host: str
    port: int = 3306
    account: str = ""
    password: str = ""
    extra_params: dict = {}


# ───────────────────────── 工具 ─────────────────────────
def _row_to_api(row: DbInstance) -> dict:
    """DbInstance 行 → 前端展示模型（对齐 db-monitor.html 契约）。"""
    try:
        m = json.loads(row.last_metrics) if row.last_metrics else {}
    except Exception:
        m = {}
    if not isinstance(m, dict):
        m = {}
    # 中文展示指标（抽屉 KPI 读取）
    disp = {}
    disp["查询延迟"] = "%.1f ms" % float(m.get("query_latency_ms", 0) or 0)
    disp["慢查询"] = "%s /min" % (m.get("slow_queries", 0) or 0)
    disp["连接使用率"] = "%.0f %%" % float(m.get("conn_usage_pct", 0) or 0)
    if row.type == "elasticsearch":
        disp["集群状态"] = m.get("cluster_status", "—")
        disp["索引数"] = m.get("indices", "—")
        disp["节点数"] = m.get("nodes", "—")
    # hero = 连接数（所有类型统一，抽屉 KPI 连接数=hero）
    try:
        hero = float(m.get("connections", 0) or 0)
    except Exception:
        hero = 0.0
    return {
        "id": row.id,
        "name": row.name,
        "type": row.type,
        "host": row.host,
        "port": row.port,
        "account": row.account,
        "status": row.status or "unknown",
        "readonly": bool(row.readonly),
        "hero": hero,
        "metrics": disp,
        "tags": (row.tags or "").split(",") if row.tags else [],
        "last_error": row.last_error or "",
        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
    }


# ───────────────────────── 只读探针（绝不写目标库） ─────────────────────────
def _probe_mysql(host, port, account, password) -> dict:
    """MySQL 只读探针：仅 SHOW / SELECT 1，无任何写操作。"""
    import pymysql
    conn = pymysql.connect(host=host, port=int(port), user=account or "root",
                           password=password or "", connect_timeout=3, read_timeout=3,
                           cursorclass=pymysql.cursors.DictCursor)
    try:
        cur = conn.cursor()
        def one(sql):
            cur.execute(sql)
            r = cur.fetchone()
            return r or {}
        c = one("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
        mx = one("SHOW VARIABLES LIKE 'max_connections'")
        q = one("SHOW GLOBAL STATUS LIKE 'Queries'")
        sl = one("SHOW GLOBAL STATUS LIKE 'Slow_queries'")
        connections = int(c.get("Value", 0) or 0)
        max_conn = int(mx.get("Value", 0) or 0) or 1
        queries = int(q.get("Value", 0) or 0)
        slow = int(sl.get("Value", 0) or 0)
        t0 = time.time()
        cur.execute("SELECT 1")
        cur.fetchone()
        latency = (time.time() - t0) * 1000
        # 逐周期差分 → 速率
        prev = _DB_PREV.get(("mysql", host, port), {})
        now = time.time()
        interval = (now - prev.get("ts", now)) if prev else 0
        qps = round((queries - prev.get("queries", queries)) / interval, 1) if interval > 0 else 0.0
        # slow_queries 统一为 int，避免与 ES 探针写入的 int 在 InfluxDB 同字段冲突
        slow_rate = int(round((slow - prev.get("slow", slow)) / (interval / 60))) if interval > 0 else 0
        _DB_PREV[("mysql", host, port)] = {"queries": queries, "slow": slow, "ts": now}
        usage = round(connections / max_conn * 100, 1)
        status = "alerting" if (usage > 85 or slow_rate > 10) else "online"
        return {
            "ok": True, "connections": connections, "max_connections": max_conn,
            "query_latency_ms": round(latency, 2), "slow_queries": slow_rate,
            "conn_usage_pct": usage, "qps": qps, "status": status,
        }
    finally:
        conn.close()


def _probe_es(host, port, account, password) -> dict:
    """Elasticsearch 只读探针：仅 GET /_cluster/health、/_nodes/stats/http、/_cat/count。绝不写索引。"""
    base = "http://%s:%s" % (host, int(port))
    auth = None
    if account:
        auth = base64.b64encode(("%s:%s" % (account, password or "")).encode()).decode()
    def get(path):
        req = urllib.request.Request(base + path)
        if auth:
            req.add_header("Authorization", "Basic " + auth)
        return urllib.request.urlopen(req, timeout=3)
    t0 = time.time()
    health = json.loads(get("/_cluster/health").read())
    latency = (time.time() - t0) * 1000
    nodes_stats = json.loads(get("/_nodes/stats/http").read())
    http_open = 0
    for n in nodes_stats.get("nodes", {}).values():
        http_open += int(n.get("http", {}).get("current_open", 0) or 0)
    status = health.get("status", "unknown")
    try:
        idx_list = json.loads(get("/_cat/indices?format=json").read())
        indices = len(idx_list) if isinstance(idx_list, list) else 0
    except Exception:
        indices = 0
    st = {"green": "online", "yellow": "alerting", "red": "exception"}.get(status, "exception")
    return {
        "ok": True, "connections": http_open, "query_latency_ms": round(latency, 2),
        "slow_queries": 0, "conn_usage_pct": 0.0, "cluster_status": status,
        "indices": indices, "nodes": health.get("number_of_nodes", 0), "status": st,
    }


def _dispatch_probe(db_type, host, port, account, password) -> dict:
    if db_type == "elasticsearch":
        return _probe_es(host, port, account, password)
    if db_type == "mysql":
        return _probe_mysql(host, port, account, password)
    # 其余类型暂未实现详细采集：标记 online 但不写指标
    return {"ok": True, "connections": 0, "query_latency_ms": 0.0, "slow_queries": 0,
            "conn_usage_pct": 0.0, "qps": 0, "status": "online", "_note": "类型暂未实现详细采集"}


# ───────────────────────── 只读采集后台任务 ─────────────────────────
async def db_collector():
    """周期只读采集所有启用实例，写 InfluxDB(db_metrics) + 回写 DbInstance 行。"""
    interval = int(os.getenv("DB_COLLECT_INTERVAL", "30"))
    while True:
        try:
            db = SessionLocal()
            try:
                insts = db.query(DbInstance).filter(DbInstance.enabled == True).all()
                for inst in insts:
                    try:
                        pw = decrypt_password(inst.password_enc)
                        res = await asyncio.to_thread(
                            _dispatch_probe, inst.type, inst.host, inst.port, inst.account, pw
                        )
                        if res.get("ok"):
                            inst.status = res.get("status", "online")
                            inst.last_error = ""
                            inst.last_metrics = json.dumps(res, ensure_ascii=False)
                            inst.last_seen = datetime.now()
                            fields = {k: v for k, v in res.items()
                                      if k not in ("ok", "status", "_note")}
                            fields["status"] = res.get("status", "online")
                            metrics_service.write_db_metrics(inst.id, inst.type, fields)
                        else:
                            inst.status = "exception"
                            inst.last_error = res.get("error", "未知错误")
                    except Exception as e:
                        msg = str(e)
                        inst.status = "offline" if ("refused" in msg.lower() or "timeout" in msg.lower()) else "exception"
                        inst.last_error = msg[:200]
                db.commit()
            finally:
                db.close()
        except Exception as e:
            print(f"[DBCollector] Error: {e}")
        await asyncio.sleep(interval)


# ═══════════════ 端点 ═══════════════
@router.get("/instances")
def list_instances(db: Session = Depends(get_db), _perm=Depends(require_any_perm("dbs"))):
    rows = db.query(DbInstance).order_by(DbInstance.id).all()
    return {"code": 0, "data": [_row_to_api(r) for r in rows], "total": len(rows)}


@router.post("/instances", status_code=201)
def create_instance(req: DbInstanceCreate, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    if db.query(DbInstance).filter(DbInstance.name == req.name).first():
        raise HTTPException(status_code=400, detail="实例名 '%s' 已存在" % req.name)
    row = DbInstance(
        name=req.name, type=req.type, host=req.host, port=req.port,
        account=req.account, password_enc=encrypt_password(req.password),
        readonly=req.readonly, enabled=req.enabled,
        tags=",".join([str(t) for t in req.tags if t]),
        extra_params=json.dumps(req.extra_params, ensure_ascii=False),
        status="unknown", last_metrics="{}",
        created_at=datetime.now(), updated_at=datetime.now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"code": 0, "message": "实例已创建", "data": _row_to_api(row)}


@router.put("/instances/{inst_id}")
def update_instance(inst_id: int, req: DbInstanceUpdate, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    row = db.query(DbInstance).filter(DbInstance.id == inst_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="实例不存在")
    data = req.model_dump(exclude_unset=True)
    if "name" in data:
        row.name = data["name"]
    if "type" in data:
        row.type = data["type"]
    if "host" in data:
        row.host = data["host"]
    if "port" in data:
        row.port = data["port"]
    if "account" in data:
        row.account = data["account"]
    if "password" in data and data["password"] is not None:
        row.password_enc = encrypt_password(data["password"])
    if "readonly" in data:
        row.readonly = data["readonly"]
    if "enabled" in data:
        row.enabled = data["enabled"]
    if "tags" in data:
        row.tags = ",".join([str(t) for t in data["tags"] if t])
    if "extra_params" in data:
        row.extra_params = json.dumps(data["extra_params"], ensure_ascii=False)
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return {"code": 0, "message": "实例已更新", "data": _row_to_api(row)}


@router.delete("/instances/{inst_id}")
def delete_instance(inst_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    row = db.query(DbInstance).filter(DbInstance.id == inst_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="实例不存在")
    db.delete(row)
    db.commit()
    return {"code": 0, "message": "实例 '%s' 已删除" % row.name}


@router.post("/test")
def test_connection(req: DbTestRequest, _admin=Depends(require_admin)):
    """只读连通性探测：绝不向目标库写入任何数据。"""
    try:
        res = _dispatch_probe(req.type, req.host, req.port, req.account, req.password)
        return {"code": 0, "ok": res.get("ok", False), "status": res.get("status", "online"),
                "metrics": res, "latency_ms": res.get("query_latency_ms", 0)}
    except Exception as e:
        return {"code": 0, "ok": False, "status": "offline",
                "error": str(e)[:200], "latency_ms": 0}


@router.get("/{inst_id}/metrics")
def get_metrics(inst_id: int, range: str = Query("1h"),
               db: Session = Depends(get_db), _perm=Depends(require_any_perm("dbs"))):
    row = db.query(DbInstance).filter(DbInstance.id == inst_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="实例不存在")
    field = HERO_FIELD.get(row.type, "connections")
    start = {"1h": "-1h", "24h": "-24h", "7d": "-7d"}.get(range, "-1h")
    series = metrics_service.query_db_history(inst_id, field, start)
    unit = {"connections": "conn", "qps": "q/s", "query_latency_ms": "ms",
            "memory_usage_pct": "%"}.get(field, "")
    return {"code": 0, "field": field, "unit": unit, "series": series}
