"""设备管理 API 路由"""
import ipaddress
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from models.database import get_db, MachineInfo
from services.crypto import encrypt_password
from models.schema import MachineCreate, MachineUpdate, MachineResponse, BatchOperation
from services.snmp_scraper import scrape_snmp
from datetime import datetime

from routers.auth import get_current_user, require_any_perm
router = APIRouter(prefix="/api/machines", tags=["设备管理"], dependencies=[Depends(get_current_user)])


@router.get("/", response_model=List[MachineResponse])
def list_machines(
    device_type: Optional[str] = Query(None),
    group_name: Optional[str] = Query(None),
    online_status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """设备列表查询"""
    q = db.query(MachineInfo)
    if device_type:
        q = q.filter(MachineInfo.device_type == device_type)
    if group_name:
        q = q.filter(MachineInfo.group_name == group_name)
    if online_status:
        q = q.filter(MachineInfo.online_status == online_status)
    if keyword:
        q = q.filter(
            (MachineInfo.name.contains(keyword)) |
            (MachineInfo.ip.contains(keyword))
        )
    return q.order_by(MachineInfo.updated_at.desc()).all()


@router.get("/{machine_id}", response_model=MachineResponse, dependencies=[Depends(require_any_perm("machines", "monitor"))])
def get_machine(machine_id: int, db: Session = Depends(get_db)):
    """获取单设备详情"""
    machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="设备不存在")
    return machine


@router.post("/", response_model=MachineResponse)
def create_machine(data: MachineCreate, db: Session = Depends(get_db)):
    """新增设备 - 注册设备，由目标机器安装 Agent/node_exporter 后主动上报"""
    existing = db.query(MachineInfo).filter(MachineInfo.ip == data.ip).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"IP {data.ip} 已存在（设备：{existing.name}），请勿重复添加")
    enc_data = data.model_dump()
    if enc_data.get("password"):
        enc_data["password"] = encrypt_password(enc_data["password"])
    if enc_data.get("pve_ssh_pass"):
        enc_data["pve_ssh_pass"] = encrypt_password(enc_data["pve_ssh_pass"])
    machine = MachineInfo(**enc_data)
    db.add(machine)
    db.commit()
    db.refresh(machine)
    return machine


@router.put("/{machine_id}", response_model=MachineResponse)
def update_machine(machine_id: int, data: MachineUpdate, db: Session = Depends(get_db)):
    """编辑设备"""
    machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="设备不存在")
    update_data = data.model_dump(exclude_unset=True)

    # 父级变更必须防环（2026-09-21）：API 可被直接调用，
    # 一旦 A.parent=B 且 B 是 A 的后代，A 及其子树会从拓扑树上「凭空消失」
    # （前端只从 roots 渲染，成环的节点既不在 roots 里也爬不到）。
    if "parent_id" in update_data:
        new_parent = update_data["parent_id"]
        if new_parent is not None:
            if new_parent == machine_id:
                raise HTTPException(status_code=400, detail="不能把设备设为它自己的父级")
            if not db.query(MachineInfo.id).filter(MachineInfo.id == new_parent).first():
                raise HTTPException(status_code=404, detail="父设备不存在")
            # 从 new_parent 沿 parent_id 向上爬，遇到自己即成环（带 visited 兜底脏数据）
            seen, cur = set(), new_parent
            while cur is not None and cur not in seen:
                if cur == machine_id:
                    raise HTTPException(status_code=400, detail="会形成循环依赖，已拒绝")
                seen.add(cur)
                row = db.query(MachineInfo.parent_id).filter(MachineInfo.id == cur).first()
                cur = row[0] if row else None

    if update_data.get("password"):
        update_data["password"] = encrypt_password(update_data["password"])
    if update_data.get("pve_ssh_pass"):
        update_data["pve_ssh_pass"] = encrypt_password(update_data["pve_ssh_pass"])
    for key, value in update_data.items():
        setattr(machine, key, value)
    machine.updated_at = datetime.now()
    db.commit()
    db.refresh(machine)
    return machine


@router.delete("/{machine_id}")
def delete_machine(machine_id: int, keep_data: bool = Query(False), db: Session = Depends(get_db)):
    """删除设备"""
    machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="设备不存在")
    # 解除子虚拟机的父子关联，避免产生指向已删父机的孤儿引用
    db.query(MachineInfo).filter(MachineInfo.parent_id == machine_id).update(
        {MachineInfo.parent_id: None}, synchronize_session=False
    )
    db.delete(machine)
    db.commit()
    return {"message": "删除成功", "keep_data": keep_data}


@router.post("/batch")
def batch_operation(op: BatchOperation, db: Session = Depends(get_db)):
    """批量操作"""
    machines = db.query(MachineInfo).filter(MachineInfo.id.in_(op.machine_ids)).all()
    count = 0
    if op.action == "delete":
        # 先解除被删设备作为父级的关联，避免产生孤儿引用
        if op.machine_ids:
            db.query(MachineInfo).filter(MachineInfo.parent_id.in_(op.machine_ids)).update(
                {MachineInfo.parent_id: None}, synchronize_session=False
            )
        for m in machines:
            db.delete(m)
            count += 1
    elif op.action == "enable":
        for m in machines:
            m.monitor_enabled = True
            count += 1
    elif op.action == "disable":
        for m in machines:
            m.monitor_enabled = False
            count += 1
    elif op.action == "change_group":
        for m in machines:
            m.group_name = op.value or "default"
            count += 1
    db.commit()
    return {"message": f"操作完成", "affected": count}


@router.get("/groups/list", dependencies=[Depends(require_any_perm("machines", "monitor"))])
def list_groups(db: Session = Depends(get_db)):
    """获取所有分组"""
    groups = db.query(MachineInfo.group_name).distinct().all()
    return [g[0] for g in groups]


@router.post("/deploy-ne/{machine_id}")
def deploy_node_exporter(machine_id: int, db: Session = Depends(get_db)):
    """通过 SSH 部署 node_exporter 到指定机器"""
    import subprocess, os
    script = "/app/deploy_221_ne.py"
    if os.path.exists(script):
        result = subprocess.run(["python3", script], capture_output=True, text=True, timeout=60)
        return {"status": "ok", "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    return {"status": "error", "message": "Script not found"}


@router.get("/parents/list", dependencies=[Depends(require_any_perm("machines", "monitor"))])
def list_parents(db: Session = Depends(get_db)):
    """获取所有可作为父级的宿主机（物理机 + PVE 虚拟化宿主机）"""
    parents = db.query(MachineInfo).filter(
        MachineInfo.device_type.in_(["physical", "pve"])
    ).all()
    return [{"id": p.id, "name": p.name, "ip": p.ip} for p in parents]


@router.get("/{machine_id}/children", response_model=List[MachineResponse], dependencies=[Depends(require_any_perm("machines", "monitor"))])
def get_children(machine_id: int, db: Session = Depends(get_db)):
    """获取物理机的所有子虚拟机"""
    children = db.query(MachineInfo).filter(
        MachineInfo.parent_id == machine_id
    ).all()
    return children


# ============ P2-9 批量纳管 ============
class MachineImportItem(BaseModel):
    name: str
    ip: str
    device_type: str = "physical"
    snmp_community: str = "public"
    snmp_version: str = "v2c"
    snmp_port: int = 161
    group_name: str = "default"
    remark: str = ""


class BatchImportRequest(BaseModel):
    items: List[MachineImportItem]


@router.post("/batch-import")
def batch_import(req: BatchImportRequest, db: Session = Depends(get_db)):
    """批量导入设备（CSV/前端表单提交）。已存在的 IP 自动跳过，不报错。"""
    created, skipped, errors = [], [], []
    existing_ips = {m.ip for m in db.query(MachineInfo.ip).all()}
    for it in req.items:
        if not it.ip or it.ip in existing_ips:
            if it.ip:
                skipped.append(it.ip)
            continue
        try:
            m = MachineInfo(
                name=it.name, ip=it.ip, device_type=it.device_type,
                snmp_community=it.snmp_community, snmp_version=it.snmp_version,
                snmp_port=it.snmp_port, group_name=it.group_name, remark=it.remark,
                monitor_enabled=True,
            )
            db.add(m)
            db.flush()
            created.append(m.id)
            existing_ips.add(it.ip)
        except Exception as e:
            errors.append({"ip": it.ip, "error": str(e)})
    db.commit()
    return {"created": created, "created_count": len(created),
            "skipped": skipped, "errors": errors}


@router.post("/batch/scan-suggest")
async def scan_suggest(
    subnet: str = Query(..., description="CIDR，例如 10.0.0.0/24"),
    community: str = Query("public"),
    version: str = Query("v2c"),
    db: Session = Depends(get_db),
):
    """网段扫描建议：对网段内每个 IP 做 SNMP 探测，返回可达且能管理（返回 sysName）的设备，
    供运维一键批量纳管，省去逐台手工录入。"""
    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        raise HTTPException(status_code=400, detail="非法网段格式")
    # 防止超大网段（如 /8、/0）生成数十亿 IP 列表导致内存耗尽（DoS）
    if net.num_addresses > 4096:
        raise HTTPException(status_code=400,
                            detail=f"网段过大（{net.num_addresses} 个地址），最多支持 /20（4096 地址），请缩小范围")
    hosts = [str(ip) for ip in net.hosts()]
    sem = asyncio.Semaphore(50)

    async def probe(ip):
        async with sem:
            try:
                res = await asyncio.to_thread(scrape_snmp, ip, 161, community, version, 0.6, 0)
            except Exception:
                return None
            if res and res.get("online"):
                return {"ip": ip, "name": res.get("sys_name", ""), "descr": (res.get("sys_descr") or "")[:120]}
            return None

    results = await asyncio.gather(*[probe(ip) for ip in hosts], return_exceptions=True)
    found = [r for r in results if isinstance(r, dict)]
    return {"subnet": subnet, "scanned": len(hosts), "found": found}
