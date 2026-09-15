"""数据库监控路由 — 只读采集 + 实例管理

端点（挂在 /api/dbs）：
  GET    /api/dbs/meta               类型目录 / 模板目录 / 默认端口（登录可读，驱动前端下拉）
  GET    /api/dbs/instances          读（require_any_perm("dbs")，admin 恒放行）
  POST   /api/dbs/instances          写（require_admin）
  PUT    /api/dbs/instances/{id}     写（require_admin）
  DELETE /api/dbs/instances/{id}     写（require_admin）
  POST   /api/dbs/test               只读连通性探测（require_admin，绝不写目标库）
  GET    /api/dbs/{id}/metrics       读（require_any_perm("dbs")，真实趋势）

支持类型（全部为只读探针，绝不向目标库写入任何数据）：
  mysql / postgresql / sqlserver / oracle / mongodb / redis / memcached
  elasticsearch / clickhouse / kafka / rabbitmq / influxdb / victoriametrics
  以及协议兼容的云托管别名：rds(MySQL) / tdsql / polardb / gaussdb(PG)

⚠ InfluxDB 字段类型约束：measurement 内同字段名全局只能有一种类型。
   本模块用 FIELD_TYPES + _coerce_fields() 统一收敛，新增字段一律登记，
   未登记的数值字段默认按 float 写入，避免跨类型 field type conflict。
"""
import os
import time
import json
import base64
import socket
import asyncio
import urllib.parse
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

# ───────────────────────── 类型目录 ─────────────────────────
# family = 实际使用的探针族（协议兼容的云托管类型直接复用）
TYPE_CATALOG = {
    "mysql":          {"t": "MySQL",            "port": 3306,  "hero": "connections",       "unit": "conn", "group": "关系型",  "family": "mysql"},
    "postgresql":     {"t": "PostgreSQL",       "port": 5432,  "hero": "qps",               "unit": "q/s",  "group": "关系型",  "family": "postgresql"},
    "sqlserver":      {"t": "SQL Server",       "port": 1433,  "hero": "connections",       "unit": "conn", "group": "关系型",  "family": "sqlserver"},
    "oracle":         {"t": "Oracle",           "port": 1521,  "hero": "sessions",          "unit": "sess", "group": "关系型",  "family": "oracle"},
    "mongodb":        {"t": "MongoDB",          "port": 27017, "hero": "connections",       "unit": "conn", "group": "NoSQL",  "family": "mongodb"},
    "redis":          {"t": "Redis",            "port": 6379,  "hero": "memory_usage_pct",  "unit": "%",    "group": "NoSQL",  "family": "redis"},
    "memcached":      {"t": "Memcached",        "port": 11211, "hero": "memory_usage_pct",  "unit": "%",    "group": "NoSQL",  "family": "memcached"},
    "elasticsearch":  {"t": "Elasticsearch",    "port": 9200,  "hero": "query_latency_ms",  "unit": "ms",   "group": "搜索/分析", "family": "elasticsearch"},
    "clickhouse":     {"t": "ClickHouse",       "port": 8123,  "hero": "qps",               "unit": "q/s",  "group": "搜索/分析", "family": "clickhouse"},
    "kafka":          {"t": "Kafka",            "port": 9092,  "hero": "partitions",        "unit": "",     "group": "消息队列", "family": "kafka"},
    "rabbitmq":       {"t": "RabbitMQ",         "port": 15672, "hero": "messages_ready",    "unit": "",     "group": "消息队列", "family": "rabbitmq"},
    "influxdb":       {"t": "InfluxDB",         "port": 8086,  "hero": "qps",               "unit": "q/s",  "group": "时序库",  "family": "influxdb"},
    "victoriametrics": {"t": "VictoriaMetrics", "port": 8428,  "hero": "qps",               "unit": "q/s",  "group": "时序库",  "family": "influxdb"},
    "rds":            {"t": "云 RDS (MySQL)",   "port": 3306,  "hero": "connections",       "unit": "conn", "group": "云托管",  "family": "mysql"},
    "tdsql":          {"t": "TDSQL (MySQL)",    "port": 3306,  "hero": "connections",       "unit": "conn", "group": "云托管",  "family": "mysql"},
    "polardb":        {"t": "PolarDB (MySQL)",  "port": 3306,  "hero": "connections",       "unit": "conn", "group": "云托管",  "family": "mysql"},
    "gaussdb":        {"t": "GaussDB (PG)",     "port": 5432,  "hero": "qps",               "unit": "q/s",  "group": "云托管",  "family": "postgresql"},
}

HERO_FIELD = {k: v["hero"] for k, v in TYPE_CATALOG.items()}

# 指标模板目录：模板 → 适用类型
TEMPLATES = [
    {"key": "default-rdb", "t": "默认关系库模板", "types": ["mysql", "postgresql", "sqlserver", "oracle", "rds", "tdsql", "polardb", "gaussdb"], "note": "连接数 / QPS / 慢查询 / 连接使用率"},
    {"key": "es-log",      "t": "ES 日志模板",    "types": ["elasticsearch"], "note": "集群状态 / 索引数 / 节点数 / 查询延迟"},
    {"key": "redis-cache", "t": "Redis 缓存模板", "types": ["redis"],         "note": "内存使用率 / 命中率 / 淘汰键 / 键总数"},
    {"key": "mongo-doc",   "t": "Mongo 文档模板", "types": ["mongodb"],       "note": "连接数 / 操作速率 / 内存 / 复制延迟"},
    {"key": "olap",        "t": "OLAP 分析模板",  "types": ["clickhouse"],    "note": "查询速率 / 写入速率 / 分区 / 合并队列"},
    {"key": "mq",          "t": "消息队列模板",   "types": ["kafka", "rabbitmq"], "note": "积压 / 吞吐 / 消费者 / 队列"},
    {"key": "cache-kv",    "t": "内存缓存模板",   "types": ["memcached", "redis"], "note": "内存使用率 / 命中率 / 淘汰数"},
    {"key": "tsdb",        "t": "时序库模板",     "types": ["influxdb", "victoriametrics"], "note": "写入速率 / Series 基数 / 内存"},
]

# ───────────────────────── 字段类型与展示口径 ─────────────────────────
# InfluxDB 同字段名只能一种类型：int 计数 / float 数值 / str 文本
FIELD_TYPES = {
    # 计数（int64）
    "connections": "int", "max_connections": "int", "indices": "int", "nodes": "int",
    "slow_queries": "int", "active_connections": "int", "blocked_locks": "int",
    "keyspace_keys": "int", "evicted_keys": "int", "blocked_clients": "int",
    "uptime_seconds": "int", "page_faults": "int", "active_reads": "int", "active_writes": "int",
    "parts": "int", "merges_in_queue": "int", "brokers": "int", "topics": "int",
    "partitions": "int", "queues": "int", "messages_ready": "int", "messages_unacked": "int",
    "consumers": "int", "blocked_sessions": "int", "sessions": "int", "evictions": "int",
    "items": "int", "series": "int", "measurements": "int",
    "memory_used_bytes": "int", "memory_total_bytes": "int", "disk_total_bytes": "int",
    # 数值（float）
    "query_latency_ms": "float", "conn_usage_pct": "float", "qps": "float", "tps": "float",
    "rollback_pct": "float", "cache_hit_pct": "float", "replication_lag": "float",
    "memory_usage_pct": "float", "hit_rate": "float", "ops_per_sec": "float",
    "insert_rate": "float", "consumer_lag": "float", "messages_per_sec": "float",
    "publish_rate": "float", "deliver_rate": "float", "batch_requests_per_sec": "float",
    "page_life_expectancy": "float", "cpu_pct": "float", "tablespace_usage_pct": "float",
    "buffer_hit_pct": "float", "executes_per_sec": "float", "writes_per_sec": "float",
    "disk_used_pct": "float", "error_rate": "float",
    # 文本
    "status": "str", "cluster_status": "str",
}

# 卡片展示字段（family → [(field, 中文label, fmt)]）
CARD = {
    "mysql": [("connections", "连接数", "int"), ("qps", "QPS", "float"), ("slow_queries", "慢查询", "permin"),
              ("query_latency_ms", "查询延迟", "ms"), ("conn_usage_pct", "连接使用率", "pct")],
    "postgresql": [("connections", "连接数", "int"), ("qps", "QPS", "float"), ("tps", "TPS", "float"),
                   ("cache_hit_pct", "缓存命中", "pct"), ("replication_lag", "主从延迟", "sec")],
    "mongodb": [("connections", "连接数", "int"), ("ops_per_sec", "操作速率", "float"),
                ("memory_usage_pct", "内存使用率", "pct"), ("replication_lag", "复制延迟", "sec"),
                ("page_faults", "页错误", "int")],
    "redis": [("connections", "连接数", "int"), ("memory_usage_pct", "内存使用率", "pct"),
              ("hit_rate", "命中率", "pct"), ("evicted_keys", "淘汰键", "int"), ("keyspace_keys", "键总数", "int")],
    "elasticsearch": [("cluster_status", "集群状态", "str"), ("nodes", "节点数", "int"), ("indices", "索引数", "int"),
                      ("query_latency_ms", "查询延迟", "ms"), ("connections", "HTTP连接", "int")],
    "clickhouse": [("qps", "查询速率", "float"), ("insert_rate", "写入速率", "float"),
                   ("connections", "连接数", "int"), ("parts", "数据分区", "int"), ("memory_usage_pct", "内存使用率", "pct")],
    "kafka": [("brokers", "Broker", "int"), ("partitions", "分区", "int"), ("topics", "Topic", "int"),
              ("consumer_lag", "消费积压", "float")],
    "rabbitmq": [("connections", "连接数", "int"), ("queues", "队列数", "int"), ("messages_ready", "待消费", "int"),
                 ("messages_unacked", "未确认", "int"), ("consumers", "消费者", "int")],
    "sqlserver": [("connections", "连接数", "int"), ("batch_requests_per_sec", "批请求/秒", "float"),
                  ("page_life_expectancy", "PLE", "sec"), ("blocked_sessions", "阻塞会话", "int"),
                  ("memory_usage_pct", "内存使用率", "pct")],
    "oracle": [("sessions", "会话数", "int"), ("connections", "连接数", "int"),
               ("tablespace_usage_pct", "表空间", "pct"), ("buffer_hit_pct", "缓冲命中", "pct"),
               ("executes_per_sec", "执行/秒", "float")],
    "memcached": [("connections", "连接数", "int"), ("memory_usage_pct", "内存使用率", "pct"),
                  ("hit_rate", "命中率", "pct"), ("evictions", "淘汰数", "int"), ("items", "Item", "int")],
    "influxdb": [("qps", "查询速率", "float"), ("writes_per_sec", "写入速率", "float"),
                 ("series", "Series", "int"), ("memory_usage_pct", "内存使用率", "pct"), ("connections", "连接数", "int")],
}

# 采集周期内存状态（计数器逐周期差分）
_DB_PREV = {}


def _rate(key, value, per_minute=False):
    """按周期差分算速率。key 需唯一（含类型/主机/端口/指标名）。首周期返回 0。"""
    try:
        value = float(value)
    except Exception:
        return 0.0
    now = time.time()
    prev = _DB_PREV.get(key)
    _DB_PREV[key] = {"v": value, "ts": now}
    if not prev:
        return 0.0
    dt = now - prev["ts"]
    if dt <= 0:
        return 0.0
    dv = value - prev["v"]
    if dv < 0:  # 计数器重置（实例重启）
        return 0.0
    return round(dv / (dt / 60.0) if per_minute else dv / dt, 1)


def _pct(part, whole):
    try:
        whole = float(whole)
        return round(float(part) / whole * 100, 1) if whole > 0 else 0.0
    except Exception:
        return 0.0


def _human_bytes(n):
    try:
        n = float(n)
    except Exception:
        return "—"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return ("%.0f %s" % (n, u)) if u in ("B", "KB") else ("%.2f %s" % (n, u))
        n /= 1024.0


def _fmt_val(v, fmt):
    if v is None or v == "":
        return "—"
    try:
        if fmt == "int":
            return str(int(round(float(v))))
        if fmt == "float":
            f = float(v)
            return str(int(f)) if abs(f - round(f)) < 1e-9 else "%.1f" % f
        if fmt == "pct":
            return "%.1f %%" % float(v)
        if fmt == "ms":
            return "%.1f ms" % float(v)
        if fmt == "sec":
            return "%.1f s" % float(v)
        if fmt == "permin":
            return "%s /min" % int(round(float(v)))
        if fmt == "bytes":
            return _human_bytes(v)
        return str(v)
    except Exception:
        return str(v)


def _coerce_fields(fields: dict) -> dict:
    """按 FIELD_TYPES 收敛类型，规避 InfluxDB 同字段名跨实例类型冲突。"""
    out = {}
    for k, v in fields.items():
        if k in ("ok", "status", "_note", "error"):
            continue
        if v is None or isinstance(v, bool):
            continue
        t = FIELD_TYPES.get(k, "float")
        try:
            if t == "int":
                out[k] = int(round(float(v)))
            elif t == "float":
                out[k] = float(v)
            else:
                out[k] = str(v)
        except Exception:
            continue
    return out


def _row_to_api(row: DbInstance) -> dict:
    """DbInstance 行 → 前端展示模型（对齐 db-monitor.html 契约）。"""
    try:
        m = json.loads(row.last_metrics) if row.last_metrics else {}
    except Exception:
        m = {}
    if not isinstance(m, dict):
        m = {}
    cat = TYPE_CATALOG.get(row.type, {})
    family = cat.get("family", row.type)
    hero_field = cat.get("hero", "connections")
    try:
        extra = json.loads(row.extra_params) if row.extra_params else {}
    except Exception:
        extra = {}
    if not isinstance(extra, dict):
        extra = {}

    disp = {}
    for fld, label, fmt in CARD.get(family, []):
        if fld in m:
            disp[label] = _fmt_val(m.get(fld), fmt)

    try:
        hero = float(m.get(hero_field, 0) or 0)
    except Exception:
        hero = 0.0
    return {
        "id": row.id,
        "name": row.name,
        "type": row.type,
        "type_label": cat.get("t", row.type),
        "host": row.host,
        "port": row.port,
        "account": row.account,
        "template": row.template or "",
        "status": row.status or "unknown",
        "readonly": bool(row.readonly),
        "enabled": bool(row.enabled),
        "hero": hero,
        "hero_label": dict((f[0], f[1]) for f in CARD.get(family, [])).get(hero_field, hero_field),
        "unit": cat.get("unit", ""),
        "metrics": disp,
        "extra": extra,
        "tags": (row.tags or "").split(",") if row.tags else [],
        "last_error": row.last_error or "",
        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
    }


# ───────────────────────── 只读探针（绝不写目标库） ─────────────────────────
def _probe_mysql(host, port, account, password, params) -> dict:
    """MySQL 只读探针：仅 SHOW / SELECT 1，无任何写操作。"""
    import pymysql
    conn = pymysql.connect(host=host, port=int(port), user=account or "root",
                           password=password or "", connect_timeout=3, read_timeout=3,
                           cursorclass=pymysql.cursors.DictCursor)
    try:
        cur = conn.cursor()

        def one(sql):
            cur.execute(sql)
            return cur.fetchone() or {}

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
        key = ("mysql", host, port)
        qps = _rate(key + ("Queries",), queries)
        slow_rate = _rate(key + ("Slow_queries",), slow, per_minute=True)
        usage = _pct(connections, max_conn)
        status = "alerting" if (usage > 85 or slow_rate > 10) else "online"
        return {
            "ok": True, "connections": connections, "max_connections": max_conn,
            "query_latency_ms": round(latency, 2), "slow_queries": slow_rate,
            "conn_usage_pct": usage, "qps": qps, "status": status,
        }
    finally:
        conn.close()


def _probe_postgresql(host, port, account, password, params) -> dict:
    """PostgreSQL 只读探针：pg_stat_* 视图查询，无任何写操作。"""
    import pg8000.native as pgn
    dbname = (params or {}).get("database") or "postgres"
    con = pgn.Connection(user=account or "postgres", host=host, port=int(port),
                         password=password or None, database=dbname, timeout=3)
    try:
        t0 = time.time()
        con.run("SELECT 1")
        latency = (time.time() - t0) * 1000

        def scalar(sql, default=0):
            try:
                r = con.run(sql)
                if not r:
                    return default
                v = r[0][0] if isinstance(r[0], (list, tuple)) else r[0]
                return default if v is None else v
            except Exception:
                return default

        connections = int(scalar("SELECT count(*) FROM pg_stat_activity") or 0)
        max_conn = int(scalar("SHOW max_connections", 100) or 100)
        commits = float(scalar("SELECT COALESCE(sum(xact_commit),0) FROM pg_stat_database") or 0)
        rollbacks = float(scalar("SELECT COALESCE(sum(xact_rollback),0) FROM pg_stat_database") or 0)
        blks_hit = float(scalar("SELECT COALESCE(sum(blks_hit),0) FROM pg_stat_database") or 0)
        blks_read = float(scalar("SELECT COALESCE(sum(blks_read),0) FROM pg_stat_database") or 0)
        blocked = int(scalar("SELECT count(*) FROM pg_locks WHERE NOT granted") or 0)
        lag = float(scalar("SELECT COALESCE(EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp())), 0)") or 0)
        active = int(scalar("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'") or 0)

        key = ("postgresql", host, port)
        qps = _rate(key + ("commit",), commits)
        roll_rate = _rate(key + ("rollback",), rollbacks)
        rollback_pct = _pct(roll_rate, (qps + roll_rate)) if (qps + roll_rate) > 0 else 0.0
        usage = _pct(connections, max_conn)
        status = "alerting" if (usage > 85 or blocked > 20) else "online"
        return {
            "ok": True, "connections": connections, "max_connections": max_conn,
            "qps": qps, "tps": qps, "active_connections": active, "blocked_locks": blocked,
            "rollback_pct": rollback_pct, "cache_hit_pct": _pct(blks_hit, blks_hit + blks_read),
            "replication_lag": round(lag, 2), "query_latency_ms": round(latency, 2),
            "slow_queries": 0, "conn_usage_pct": usage, "status": status,
        }
    finally:
        try:
            con.close()
        except Exception:
            pass


def _probe_mongodb(host, port, account, password, params) -> dict:
    """MongoDB 只读探针：serverStatus / replSetGetStatus / hostInfo，无任何写操作。"""
    from pymongo import MongoClient
    kw = dict(host=host, port=int(port), serverSelectionTimeoutMS=3000,
              connectTimeoutMS=3000, socketTimeoutMS=4000, directConnection=True)
    authsrc = (params or {}).get("authSource") or "admin"
    if account:
        kw.update(username=account, password=password or "", authSource=authsrc)
    cli = MongoClient(**kw)
    try:
        t0 = time.time()
        cli.admin.command("ping")
        latency = (time.time() - t0) * 1000
        st = cli.admin.command("serverStatus")
        conns = int(st.get("connections", {}).get("current", 0) or 0)
        opc = st.get("opcounters", {}) or {}
        ops = sum(float(opc.get(k, 0) or 0) for k in ("insert", "query", "update", "delete"))
        mem = st.get("mem", {}) or {}
        resident_mb = float(mem.get("resident", 0) or 0)
        used_bytes = int(resident_mb * 1024 * 1024)
        total_bytes = 0
        try:
            hi = cli.admin.command("hostInfo")
            total_bytes = int((hi.get("system", {}) or {}).get("memSizeMb", 0) or 0) * 1024 * 1024
        except Exception:
            pass
        page_faults = int((st.get("extra_info", {}) or {}).get("page_faults", 0) or 0)
        uptime = int(st.get("uptime", 0) or 0)
        reads = int((st.get("globalLock", {}) or {}).get("activeClients", {}).get("readers", 0) or 0)
        writes = int((st.get("globalLock", {}) or {}).get("activeClients", {}).get("writers", 0) or 0)
        lag = 0.0
        try:
            rs = cli.admin.command("replSetGetStatus")
            primary = next((m for m in rs.get("members", []) if m.get("stateStr") == "PRIMARY"), None)
            if primary and primary.get("optimeDate"):
                base = primary["optimeDate"]
                lags = [(base - m["optimeDate"]).total_seconds()
                        for m in rs.get("members", []) if m.get("optimeDate") and m.get("stateStr") == "SECONDARY"]
                lag = round(max(lags), 1) if lags else 0.0
        except Exception:
            pass
        ops_per_sec = _rate(("mongodb", host, port, "ops"), ops)
        usage = _pct(used_bytes, total_bytes)
        status = "alerting" if usage > 90 else "online"
        return {
            "ok": True, "connections": conns, "ops_per_sec": ops_per_sec,
            "memory_used_bytes": used_bytes, "memory_total_bytes": total_bytes,
            "memory_usage_pct": usage, "page_faults": page_faults, "replication_lag": lag,
            "active_reads": reads, "active_writes": writes, "uptime_seconds": uptime,
            "query_latency_ms": round(latency, 2), "slow_queries": 0, "conn_usage_pct": 0.0,
            "status": status,
        }
    finally:
        try:
            cli.close()
        except Exception:
            pass


class _Redis:
    """最小 RESP 客户端（纯标准库 socket），只用于只读命令（AUTH/SELECT/INFO）。"""

    def __init__(self, host, port, timeout=3):
        self.s = socket.create_connection((host, int(port)), timeout=timeout)
        self.s.settimeout(timeout)
        self.buf = b""

    def _fill(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(4096)
            if not chunk:
                raise IOError("redis: connection closed")
            self.buf += chunk

    def _line(self):
        while b"\r\n" not in self.buf:
            self._fill(len(self.buf) + 1)
        i = self.buf.index(b"\r\n")
        s, self.buf = self.buf[:i], self.buf[i + 2:]
        return s

    def _parse(self):
        self._fill(1)
        t, self.buf = self.buf[:1], self.buf[1:]
        if t == b"+":
            return self._line()
        if t == b"-":
            raise RuntimeError(self._line().decode("utf-8", "replace"))
        if t == b":":
            return int(self._line())
        if t == b"$":
            n = int(self._line())
            if n < 0:
                return None
            self._fill(n + 2)
            v, self.buf = self.buf[:n], self.buf[n + 2:]
            return v
        if t == b"*":
            n = int(self._line())
            return [self._parse() for _ in range(max(0, n))]
        raise IOError("redis: bad RESP type %r" % t)

    def cmd(self, *args):
        out = b"*%d\r\n" % len(args)
        for a in args:
            b = a if isinstance(a, bytes) else str(a).encode()
            out += b"$%d\r\n" % len(b) + b + b"\r\n"
        self.s.sendall(out)
        return self._parse()

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def _probe_redis(host, port, account, password, params) -> dict:
    """Redis 只读探针：AUTH / SELECT / INFO，绝不执行任何写命令。"""
    r = _Redis(host, port)
    try:
        if password:
            try:
                r.cmd("AUTH", account or "default", password)   # Redis 6+ ACL
            except Exception:
                r.cmd("AUTH", password)                         # 旧版单参数
        db = int((params or {}).get("database", 0) or 0)
        if db:
            r.cmd("SELECT", db)
        t0 = time.time()
        raw = r.cmd("INFO")
        latency = (time.time() - t0) * 1000
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        info = {}
        for ln in text.splitlines():
            if ":" in ln and not ln.startswith("#"):
                k, v = ln.split(":", 1)
                info[k.strip()] = v.strip()

        def g(k, d=0.0):
            try:
                return float(info.get(k, d) or d)
            except Exception:
                return float(d)

        conns = int(g("connected_clients"))
        used = int(g("used_memory"))
        maxmem = int(g("maxmemory"))
        total = maxmem if maxmem > 0 else int(g("total_system_memory"))
        hits, misses = g("keyspace_hits"), g("keyspace_misses")
        keys = 0
        for k, v in info.items():
            if k.startswith("db") and "keys=" in v:
                for part in v.split(","):
                    if part.startswith("keys="):
                        try:
                            keys += int(part.split("=")[1])
                        except Exception:
                            pass
        ops = g("instantaneous_ops_per_sec")
        lag = 0.0
        if info.get("role") == "slave" and info.get("master_link_status") == "up":
            lag = round(g("master_last_io_seconds_ago"), 1)
        usage = _pct(used, total)
        return {
            "ok": True, "connections": conns, "memory_used_bytes": used,
            "memory_total_bytes": total, "memory_usage_pct": usage,
            "hit_rate": _pct(hits, hits + misses), "keyspace_keys": keys,
            "evicted_keys": int(g("evicted_keys")), "blocked_clients": int(g("blocked_clients")),
            "ops_per_sec": ops, "qps": ops, "replication_lag": lag,
            "uptime_seconds": int(g("uptime_in_seconds")),
            "query_latency_ms": round(latency, 2), "slow_queries": 0, "conn_usage_pct": 0.0,
            "status": "alerting" if usage > 90 else "online",
        }
    finally:
        r.close()



def _probe_es(host, port, account, password, params) -> dict:
    """Elasticsearch 只读探针：仅 GET 集群健康 / 节点统计 / 索引列表。绝不写索引。"""
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


def _probe_clickhouse(host, port, account, password, params) -> dict:
    """ClickHouse 只读探针：HTTP 接口跑 SELECT，只读 system 表。"""
    base = "http://%s:%s/" % (host, int(port))

    def q(sql):
        req = urllib.request.Request(base + "?query=" + urllib.parse.quote(sql))
        if account:
            req.add_header("X-ClickHouse-User", account)
            req.add_header("X-ClickHouse-Key", password or "")
        return urllib.request.urlopen(req, timeout=3).read().decode("utf-8", "replace").strip()

    t0 = time.time()
    q("SELECT 1")
    latency = (time.time() - t0) * 1000

    def metric(name):
        try:
            return float(q("SELECT value FROM system.metrics WHERE metric='%s'" % name) or 0)
        except Exception:
            return 0.0

    def one(sql, d=0.0):
        try:
            return float(q(sql) or 0)
        except Exception:
            return float(d)

    memory_tracking = one("SELECT value FROM system.metrics WHERE metric='MemoryTracking'")
    total_mem = one("SELECT value FROM system.asynchronous_metrics WHERE metric='OSMemoryTotal'")
    key = ("clickhouse", host, port)
    qps = _rate(key + ("Query",), metric("Query"))
    insert_rate = _rate(key + ("InsertQuery",), metric("InsertQuery"))
    usage = _pct(memory_tracking, total_mem)
    return {
        "ok": True, "connections": int(metric("TCPConnection")), "qps": qps,
        "insert_rate": insert_rate, "parts": int(one("SELECT count() FROM system.parts WHERE active")),
        "merges_in_queue": int(metric("BackgroundMergesAndMutationsPoolTask")),
        "memory_used_bytes": int(memory_tracking), "memory_total_bytes": int(total_mem),
        "memory_usage_pct": usage, "uptime_seconds": int(one("SELECT uptime()")),
        "query_latency_ms": round(latency, 2), "slow_queries": 0, "conn_usage_pct": 0.0,
        "status": "alerting" if usage > 90 else "online",
    }


def _probe_kafka(host, port, account, password, params) -> dict:
    """Kafka 只读探针：Admin 元数据查询（broker/topic/partition）。
    消费积压需显式指定 extra_params.group（单组），避免逐组轮询拖慢采集周期。"""
    from kafka.admin import KafkaAdminClient
    boot = "%s:%d" % (host, int(port))
    admin = KafkaAdminClient(bootstrap_servers=boot, request_timeout_ms=3000,
                             client_id="monitor-platform")
    try:
        t0 = time.time()
        try:
            info = admin.describe_cluster()
            brokers = len(info.get("brokers", []) or [])
        except Exception:
            try:
                brokers = len(admin._client.cluster.brokers())
            except Exception:
                brokers = 0
        latency = (time.time() - t0) * 1000
        topics = list(admin.list_topics() or [])
        partitions = 0
        try:
            for d in admin.describe_topics(topics[:200]):
                partitions += len(d.get("partitions", []) or [])
        except Exception:
            pass
        lag = 0.0
        group = (params or {}).get("group")
        if group:
            try:
                from kafka import KafkaConsumer
                consumer = KafkaConsumer(bootstrap_servers=boot, request_timeout_ms=3000,
                                         consumer_timeout_ms=3000)
                offs = admin.list_consumer_group_offsets(group)
                ends = consumer.end_offsets(list(offs.keys())) if offs else {}
                for tp, om in offs.items():
                    lag += max(0, int(ends.get(tp, 0)) - int(getattr(om, "offset", 0) or 0))
                consumer.close()
            except Exception:
                lag = 0.0
        return {
            "ok": True, "brokers": brokers, "topics": len(topics), "partitions": partitions,
            "consumer_lag": float(lag), "connections": brokers,
            "messages_per_sec": 0.0, "query_latency_ms": round(latency, 2),
            "slow_queries": 0, "conn_usage_pct": 0.0,
            "status": "alerting" if lag > 10000 else "online",
        }
    finally:
        try:
            admin.close()
        except Exception:
            pass


def _probe_rabbitmq(host, port, account, password, params) -> dict:
    """RabbitMQ 只读探针：management HTTP API（默认 15672），只 GET。"""
    base = "http://%s:%d" % (host, int(port))
    auth = base64.b64encode(("%s:%s" % (account, password or "")).encode()).decode()

    def get(path):
        req = urllib.request.Request(base + path)
        req.add_header("Authorization", "Basic " + auth)
        return json.loads(urllib.request.urlopen(req, timeout=3).read())

    t0 = time.time()
    ov = get("/api/overview")
    latency = (time.time() - t0) * 1000
    tot = ov.get("object_totals", {}) or {}
    ms = ov.get("message_stats", {}) or {}
    ready = unacked = 0
    try:
        qs = get("/api/queues?page=1&page_size=500")
        for q in (qs.get("items", []) if isinstance(qs, dict) else qs):
            ready += int(q.get("messages_ready", 0) or 0)
            unacked += int(q.get("messages_unacknowledged", 0) or 0)
    except Exception:
        pass
    pub = float((ms.get("publish_details", {}) or {}).get("rate", 0) or 0)
    dlv = float((ms.get("deliver_get_details", {}) or {}).get("rate", 0) or 0)
    return {
        "ok": True, "connections": int(tot.get("connections", 0) or 0),
        "queues": int(tot.get("queues", 0) or 0), "consumers": int(tot.get("consumers", 0) or 0),
        "messages_ready": ready, "messages_unacked": unacked,
        "publish_rate": round(pub, 1), "deliver_rate": round(dlv, 1), "qps": round(dlv, 1),
        "query_latency_ms": round(latency, 2), "slow_queries": 0, "conn_usage_pct": 0.0,
        "status": "alerting" if ready > 50000 else "online",
    }


def _probe_sqlserver(host, port, account, password, params) -> dict:
    """SQL Server 只读探针：DMV 查询（python-tds），只读。"""
    import pytds
    db = (params or {}).get("database") or "master"
    con = pytds.connect(server=host, port=int(port), user=account, password=password or "",
                        database=db, login_timeout=3, timeout=3)
    try:
        cur = con.cursor()

        def one(sql, d=0):
            try:
                cur.execute(sql)
                r = cur.fetchone()
                return (r[0] if r and r[0] is not None else d)
            except Exception:
                return d

        t0 = time.time()
        cur.execute("SELECT 1")
        cur.fetchone()
        latency = (time.time() - t0) * 1000
        conns = int(one("SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1") or 0)
        blocked = int(one("SELECT COUNT(*) FROM sys.dm_exec_requests WHERE blocking_session_id <> 0") or 0)
        batch = float(one("SELECT cntr_value FROM sys.dm_os_performance_counters "
                          "WHERE counter_name = 'Batch Requests/sec'") or 0)
        ple = float(one("SELECT cntr_value FROM sys.dm_os_performance_counters "
                        "WHERE counter_name = 'Page life expectancy'") or 0)
        mem_used = float(one("SELECT committed_kb * 1024.0 FROM sys.dm_os_sys_info") or 0)
        mem_total = float(one("SELECT total_physical_memory_kb * 1024.0 FROM sys.dm_os_sys_info") or 0)
        cpu = float(one("SELECT cntr_value FROM sys.dm_os_performance_counters "
                        "WHERE counter_name = 'CPU usage %' AND object_name LIKE '%SQLServer%'") or 0)
        key = ("sqlserver", host, port)
        return {
            "ok": True, "connections": conns, "blocked_sessions": blocked,
            "batch_requests_per_sec": _rate(key + ("batch",), batch),
            "page_life_expectancy": round(ple, 1), "memory_used_bytes": int(mem_used),
            "memory_total_bytes": int(mem_total), "memory_usage_pct": _pct(mem_used, mem_total),
            "cpu_pct": round(cpu, 1), "qps": 0.0, "query_latency_ms": round(latency, 2),
            "slow_queries": 0, "conn_usage_pct": 0.0,
            "status": "alerting" if blocked > 20 else "online",
        }
    finally:
        try:
            con.close()
        except Exception:
            pass


def _probe_oracle(host, port, account, password, params) -> dict:
    """Oracle 只读探针：v$ 视图（oracledb thin 模式），只读。"""
    import oracledb
    p = params or {}
    if p.get("service_name") or p.get("sid"):
        dsn = oracledb.makedsn(host, int(port),
                               service_name=p.get("service_name") or p.get("sid"))
    else:
        dsn = oracledb.makedsn(host, int(port), sid=p.get("sid", "ORCL"))
    con = oracledb.connect(user=account, password=password or "", dsn=dsn)
    try:
        cur = con.cursor()

        def one(sql, d=0):
            try:
                cur.execute(sql)
                r = cur.fetchone()
                return (r[0] if r and r[0] is not None else d)
            except Exception:
                return d

        t0 = time.time()
        cur.execute("SELECT 1 FROM DUAL")
        cur.fetchone()
        latency = (time.time() - t0) * 1000
        sessions = int(one("SELECT COUNT(*) FROM v$session") or 0)
        users = int(one("SELECT COUNT(*) FROM v$session WHERE type = 'USER'") or 0)
        logical = float(one("SELECT value FROM v$sysstat WHERE name = 'session logical reads'") or 0)
        physical = float(one("SELECT value FROM v$sysstat WHERE name = 'physical reads'") or 0)
        execs = float(one("SELECT value FROM v$sysstat WHERE name = 'execute count'") or 0)
        ts = float(one("SELECT NVL(MAX(used_percent),0) FROM dba_tablespace_usage_metrics") or 0)
        key = ("oracle", host, port)
        return {
            "ok": True, "sessions": sessions, "connections": users,
            "tablespace_usage_pct": round(ts, 1),
            "buffer_hit_pct": round((1 - _pct(physical, logical) / 100) * 100, 1) if logical > 0 else 0.0,
            "executes_per_sec": _rate(key + ("exec",), execs),
            "qps": _rate(key + ("exec",), execs), "query_latency_ms": round(latency, 2),
            "slow_queries": 0, "conn_usage_pct": 0.0,
            "status": "alerting" if ts > 90 else "online",
        }
    finally:
        try:
            con.close()
        except Exception:
            pass


def _probe_memcached(host, port, account, password, params) -> dict:
    """Memcached 只读探针：文本协议 stats 命令，只读。"""
    s = socket.create_connection((host, int(port)), timeout=3)
    s.settimeout(3)
    try:
        t0 = time.time()
        s.sendall(b"stats\r\n")
        buf = b""
        while b"END\r\n" not in buf:
            chunk = s.recv(8192)
            if not chunk:
                break
            buf += chunk
        latency = (time.time() - t0) * 1000
        st = {}
        for ln in buf.decode("utf-8", "replace").splitlines():
            if ln.startswith("STAT "):
                parts = ln.split()
                if len(parts) >= 3:
                    st[parts[1]] = parts[2]

        def g(k, d=0.0):
            try:
                return float(st.get(k, d) or d)
            except Exception:
                return float(d)

        used, total = g("bytes"), g("limit_maxbytes")
        hits, misses = g("get_hits"), g("get_misses")
        usage = _pct(used, total)
        return {
            "ok": True, "connections": int(g("curr_connections")),
            "memory_used_bytes": int(used), "memory_total_bytes": int(total),
            "memory_usage_pct": usage, "hit_rate": _pct(hits, hits + misses),
            "evictions": int(g("evictions")), "items": int(g("curr_items")),
            "ops_per_sec": 0.0, "uptime_seconds": int(g("uptime")), "qps": 0.0,
            "query_latency_ms": round(latency, 2), "slow_queries": 0, "conn_usage_pct": 0.0,
            "status": "alerting" if usage > 90 else "online",
        }
    finally:
        try:
            s.close()
        except Exception:
            pass


def _probe_influxdb(host, port, account, password, params) -> dict:
    """InfluxDB / VictoriaMetrics 只读探针：/health + /metrics(Prometheus 文本)。只读。"""
    base = "http://%s:%d" % (host, int(port))
    auth = None
    if account:
        auth = base64.b64encode(("%s:%s" % (account, password or "")).encode()).decode()

    def fetch(path):
        req = urllib.request.Request(base + path)
        if auth:
            req.add_header("Authorization", "Basic " + auth)
        return urllib.request.urlopen(req, timeout=3)

    t0 = time.time()
    try:
        fetch("/health")
    except Exception:
        fetch("/ping")
    latency = (time.time() - t0) * 1000

    metrics = {}
    try:
        text = fetch("/metrics").read().decode("utf-8", "replace")
        for ln in text.splitlines():
            if not ln or ln.startswith("#"):
                continue
            name = ln.split(" ", 1)[0].split("{")[0]
            try:
                val = float(ln.rsplit(" ", 1)[1])
            except Exception:
                continue
            metrics[name] = metrics.get(name, 0.0) + val
    except Exception:
        pass

    def mv(*names):
        for n in names:
            if n in metrics:
                return float(metrics[n])
        return 0.0

    http_req = mv("http_api_requests_total", "vm_http_requests_total", "influxdb_http_requests_total")
    writes = mv("influxdb_write_requests_total", "vm_rows_inserted_total")
    series = mv("influxdb_series_num", "vm_series_count")
    mem_used = mv("influxdb_memory_usage_bytes", "process_resident_memory_bytes")
    uptime = mv("influxdb_uptime_seconds", "vm_uptime_seconds")
    key = ("influxdb", host, port)
    return {
        "ok": True, "connections": int(mv("influxdb_connections", "http_active_connections")),
        "qps": _rate(key + ("http",), http_req), "writes_per_sec": _rate(key + ("write",), writes),
        "series": int(series), "memory_used_bytes": int(mem_used),
        "memory_usage_pct": 0.0, "uptime_seconds": int(uptime),
        "query_latency_ms": round(latency, 2), "slow_queries": 0, "conn_usage_pct": 0.0,
        "status": "online",
    }


_PROBES = {
    "mysql": _probe_mysql,
    "postgresql": _probe_postgresql,
    "mongodb": _probe_mongodb,
    "redis": _probe_redis,
    "elasticsearch": _probe_es,
    "clickhouse": _probe_clickhouse,
    "kafka": _probe_kafka,
    "rabbitmq": _probe_rabbitmq,
    "sqlserver": _probe_sqlserver,
    "oracle": _probe_oracle,
    "memcached": _probe_memcached,
    "influxdb": _probe_influxdb,
}

_OFFLINE_HINTS = ("refused", "timeout", "timed out", "unreachable", "getaddrinfo",
                  "name or service", "no route", "network is unreachable", "连接被拒绝")


def _dispatch_probe(db_type, host, port, account, password, params=None) -> dict:
    """按类型（或协议族）分发只读探针；异常统一降级为结构化错误。"""
    family = TYPE_CATALOG.get(db_type, {}).get("family", db_type)
    fn = _PROBES.get(family)
    if fn is None:
        return {"ok": False, "offline": False, "error": "不支持的数据库类型: %s" % db_type}
    try:
        return fn(host, port, account, password, params or {})
    except ImportError as e:
        return {"ok": False, "offline": False,
                "error": "缺少 %s 驱动依赖: %s（需重建后端镜像）" % (family, e)}
    except Exception as e:
        msg = str(e)[:200] or e.__class__.__name__
        offline = any(h in msg.lower() for h in _OFFLINE_HINTS)
        return {"ok": False, "offline": offline, "error": msg}


# ───────────────────────── 请求模型 ─────────────────────────
class DbInstanceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    type: str = Field(default="mysql")
    host: str = Field(..., min_length=1, max_length=128)
    port: int = Field(default=3306, ge=1, le=65535)
    account: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)
    template: str = Field(default="", max_length=64)
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
    template: Optional[str] = None
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
                        params = {}
                        try:
                            params = json.loads(inst.extra_params) if inst.extra_params else {}
                        except Exception:
                            params = {}
                        if not isinstance(params, dict):
                            params = {}
                        res = await asyncio.to_thread(
                            _dispatch_probe, inst.type, inst.host, inst.port,
                            inst.account, pw, params
                        )
                        if res.get("ok"):
                            inst.status = res.get("status", "online")
                            inst.last_error = ""
                            inst.last_metrics = json.dumps(res, ensure_ascii=False)
                            inst.last_seen = datetime.now()
                            fields = _coerce_fields(res)
                            fields["status"] = res.get("status", "online")
                            metrics_service.write_db_metrics(inst.id, inst.type, fields)
                        else:
                            inst.status = "offline" if res.get("offline") else "exception"
                            inst.last_error = res.get("error", "未知错误")
                    except Exception as e:
                        msg = str(e)
                        inst.status = "offline" if any(h in msg.lower() for h in _OFFLINE_HINTS) else "exception"
                        inst.last_error = msg[:200]
                db.commit()
            finally:
                db.close()
        except Exception as e:
            print(f"[DBCollector] Error: {e}")
        await asyncio.sleep(interval)


# ═══════════════ 端点 ═══════════════
@router.get("/meta")
def get_meta():
    """类型目录 / 模板目录：驱动前端下拉与默认端口。登录即可读。"""
    types = []
    for key, v in TYPE_CATALOG.items():
        types.append({"key": key, "label": v["t"], "port": v["port"], "hero": v["hero"],
                      "unit": v["unit"], "group": v["group"], "family": v["family"],
                      "card": CARD.get(v["family"], [])})
    return {"code": 0, "types": types, "templates": TEMPLATES,
            "field_types": FIELD_TYPES}


@router.get("/instances")
def list_instances(db: Session = Depends(get_db), _perm=Depends(require_any_perm("dbs"))):
    rows = db.query(DbInstance).order_by(DbInstance.id).all()
    return {"code": 0, "data": [_row_to_api(r) for r in rows], "total": len(rows)}


@router.post("/instances", status_code=201)
def create_instance(req: DbInstanceCreate, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    if db.query(DbInstance).filter(DbInstance.name == req.name).first():
        raise HTTPException(status_code=400, detail="实例名 '%s' 已存在" % req.name)
    if req.type not in TYPE_CATALOG:
        raise HTTPException(status_code=400, detail="不支持的类型 '%s'" % req.type)
    row = DbInstance(
        name=req.name, type=req.type, host=req.host, port=req.port,
        account=req.account, password_enc=encrypt_password(req.password),
        template=req.template or "", readonly=req.readonly, enabled=req.enabled,
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
    if data.get("type") and data["type"] not in TYPE_CATALOG:
        raise HTTPException(status_code=400, detail="不支持的类型 '%s'" % data["type"])
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
    if "password" in data and data["password"] is not None and data["password"] != "":
        row.password_enc = encrypt_password(data["password"])
    if "template" in data:
        row.template = data["template"] or ""
    if "readonly" in data:
        row.readonly = data["readonly"]
    if "enabled" in data:
        row.enabled = data["enabled"]
    if "tags" in data:
        row.tags = ",".join([str(t) for t in data["tags"] if t])
    if "extra_params" in data:
        row.extra_params = json.dumps(data["extra_params"], ensure_ascii=False)
    row.status = "unknown"
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return {"code": 0, "message": "实例已更新", "data": _row_to_api(row)}


@router.delete("/instances/{inst_id}")
def delete_instance(inst_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    row = db.query(DbInstance).filter(DbInstance.id == inst_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="实例不存在")
    name = row.name
    db.delete(row)
    db.commit()
    return {"code": 0, "message": "实例 '%s' 已删除" % name}


@router.post("/test")
def test_connection(req: DbTestRequest, _admin=Depends(require_admin)):
    """只读连通性探测：绝不向目标库写入任何数据。"""
    if req.type not in TYPE_CATALOG:
        return {"code": 0, "ok": False, "status": "exception",
                "error": "不支持的类型 '%s'" % req.type, "latency_ms": 0}
    res = _dispatch_probe(req.type, req.host, req.port, req.account, req.password,
                          req.extra_params or {})
    if not res.get("ok"):
        return {"code": 0, "ok": False,
                "status": "offline" if res.get("offline") else "exception",
                "error": res.get("error", "探测失败"), "latency_ms": 0}
    return {"code": 0, "ok": True, "status": res.get("status", "online"),
            "metrics": res, "latency_ms": res.get("query_latency_ms", 0)}


@router.get("/{inst_id}/metrics")
def get_metrics(inst_id: int, range: str = Query("1h"),
               db: Session = Depends(get_db), _perm=Depends(require_any_perm("dbs"))):
    row = db.query(DbInstance).filter(DbInstance.id == inst_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="实例不存在")
    field = HERO_FIELD.get(row.type, "connections")
    start = {"1h": "-1h", "24h": "-24h", "7d": "-7d"}.get(range, "-1h")
    series = metrics_service.query_db_history(inst_id, field, start)
    unit = TYPE_CATALOG.get(row.type, {}).get("unit", "")
    return {"code": 0, "field": field, "unit": unit, "series": series}
