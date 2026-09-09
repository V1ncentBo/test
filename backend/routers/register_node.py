"""Node Exporter 自助注册接口（免登录）

设计目标：让 install-node-exporter.sh 在「零输入」场景下也能把设备注册进平台，
从而被 node_exporter_collector 自动采集上线。

安全模型：
- 全局 SlowAPIMiddleware 已提供 200/min 兜底限速；
- 本路由再叠加「每 IP 10 次/分钟」的内存限速，防止刷库；
- 可选密钥：部署时在环境变量设 NODE_REGISTER_SECRET，脚本用 --secret 传入。
  不设则开放注册（满足零输入需求）。密钥仅用于挡住未授权写入，不提供机密性。
"""
import os
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.database import get_db, MachineInfo

router = APIRouter(prefix="/api/register-node", tags=["Node自助注册"])

NODE_REGISTER_SECRET = (os.getenv("NODE_REGISTER_SECRET") or "").strip()

# ---- 轻量内存限速（每 IP 窗口内次数） ----
_RATE = defaultdict(list)
_RATE_LIMIT = int(os.getenv("NODE_REGISTER_RATE_LIMIT", "10"))
_RATE_WINDOW = 60  # 秒


def _rate_ok(ip: str) -> bool:
    now = time.time()
    hits = _RATE[ip]
    # 清理窗口外的请求时间戳
    _RATE[ip] = [t for t in hits if now - t < _RATE_WINDOW]
    if len(_RATE[ip]) >= _RATE_LIMIT:
        return False
    _RATE[ip].append(now)
    return True


class NodeRegisterReq(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    port: int = 9100
    group_name: str = "default"
    device_type: str = "node_exporter"
    secret: Optional[str] = None


@router.post("")
def register_node(req: NodeRegisterReq, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else None

    # 1. 频率限制（按客户端 IP）
    if not _rate_ok(client_ip or "unknown"):
        raise HTTPException(status_code=429, detail="注册过于频繁，请稍后再试")

    # 2. 可选密钥校验
    if NODE_REGISTER_SECRET:
        if not req.secret or req.secret != NODE_REGISTER_SECRET:
            raise HTTPException(status_code=403, detail="需要正确的注册密钥 (NODE_REGISTER_SECRET)")

    # 3. 确定设备 IP：优先 body.ip，否则取客户端 IP
    ip = (req.ip or "").strip() or client_ip
    if not ip:
        raise HTTPException(status_code=400, detail="无法确定设备 IP，请在请求体中显式传入 ip")

    if not (1 <= len(ip) <= 45):
        raise HTTPException(status_code=400, detail="IP 格式不合法")

    name = (req.name or "").strip() or f"node-{ip}"

    # 4. 查重：已存在则更新为 node_exporter 监控，避免重复登记
    existing = db.query(MachineInfo).filter(MachineInfo.ip == ip).first()
    if existing:
        existing.device_type = req.device_type
        existing.port = req.port
        existing.group_name = req.group_name
        existing.monitor_enabled = True
        existing.online_status = "unknown"
        existing.updated_at = datetime.now()
        db.commit()
        db.refresh(existing)
        return {
            "ok": True,
            "status": "updated",
            "machine_id": existing.id,
            "ip": ip,
            "name": existing.name,
            "monitor_enabled": True,
            "message": "设备已存在，已更新为 node_exporter 监控",
        }

    # 5. 新建
    machine = MachineInfo(
        name=name,
        ip=ip,
        port=req.port,
        device_type=req.device_type,
        group_name=req.group_name,
        username="root",
        password="",
        monitor_enabled=True,
        online_status="unknown",
    )
    db.add(machine)
    db.commit()
    db.refresh(machine)
    return {
        "ok": True,
        "status": "created",
        "machine_id": machine.id,
        "ip": ip,
        "name": machine.name,
        "monitor_enabled": True,
        "message": "设备注册成功，约 30 秒后被采集器自动拉取",
    }
