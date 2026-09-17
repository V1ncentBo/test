"""资源管理台账路由（私有台账，与蓝鲸 CMDB 完全隔离）
提供：资源列表/详情（任意登录用户可读）、新建/编辑/删除/转交（仅管理员）、
评论/关注（任意登录用户可协作）、一次性种子数据（仅管理员）。
所有端点挂在 /api/resource-cmdb 下。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import func
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from models.database import get_db
from models.resource_cmdb import ResourceItem, Cabinet
from routers.auth import get_current_user, require_admin
from services.crypto import encrypt_password, decrypt_password

router = APIRouter(
    prefix="/api/resource-cmdb",
    tags=["资源管理台账"],
    dependencies=[Depends(get_current_user)],
)


# ---------------- 序列化 ----------------
def to_dict(r: ResourceItem) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "pve_ip": r.pve_ip,
        "vm_ip": r.vm_ip,
        "domain": r.domain,
        "owner": r.owner,
        "projects": r.projects or [],
        "machine_type": r.machine_type,
        "category": r.category or "vm",
        "location": r.location,
        "office_area": r.office_area or "",
        "expire_at": r.expire_at,
        "alert_count": r.alert_count,
        "status": r.status,
        "remark": r.remark,
        "account": r.account,
        "password": decrypt_password(r.password) if r.password else "",
        "machine_id": r.machine_id,
        "cabinet_id": r.cabinet_id,
        "ru_position": r.ru_position,
        "ru_height": r.ru_height if r.ru_height else 1,
        "followers": r.followers or [],
        "comments": r.comments or [],
        "timeline": r.timeline or [],
        "creator": r.creator,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else "",
    }


def now_short() -> str:
    d = datetime.now()
    return f"{d.month:02d}-{d.day:02d} {d.hour:02d}:{d.minute:02d}"


# ---------------- 请求模型 ----------------
class ResourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    pve_ip: str = ""
    vm_ip: str = ""
    domain: str = ""
    owner: str = ""
    projects: List[str] = Field(default_factory=list)
    machine_type: str = ""
    category: str = "vm"
    location: str = ""
    office_area: str = ""
    expire_at: str = ""
    alert_count: int = 0
    status: str = "online"
    remark: str = ""
    account: str = ""
    password: str = ""
    machine_id: Optional[int] = None
    cabinet_id: Optional[int] = None
    ru_position: Optional[int] = None
    ru_height: int = 1


class ResourceUpdate(BaseModel):
    name: Optional[str] = None
    pve_ip: Optional[str] = None
    vm_ip: Optional[str] = None
    domain: Optional[str] = None
    owner: Optional[str] = None
    projects: Optional[List[str]] = None
    machine_type: Optional[str] = None
    category: Optional[str] = None
    location: Optional[str] = None
    office_area: Optional[str] = None
    expire_at: Optional[str] = None
    alert_count: Optional[int] = None
    status: Optional[str] = None
    remark: Optional[str] = None
    account: Optional[str] = None
    password: Optional[str] = None
    machine_id: Optional[int] = None
    cabinet_id: Optional[int] = None
    ru_position: Optional[int] = None
    ru_height: Optional[int] = None


class TransferBody(BaseModel):
    owner: str = Field(..., min_length=1)


class CommentBody(BaseModel):
    text: str = Field(..., min_length=1)


# ---------------- 读取（任意登录用户） ----------------
@router.get("/resources", summary="列出资源（可按 category=physical/vm 过滤）")
def list_resources(category: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(ResourceItem)
    if category:
        q = q.filter(ResourceItem.category == category)
    rows = q.order_by(ResourceItem.id).all()
    return [to_dict(r) for r in rows]


@router.get("/resources/{rid}", summary="获取单个资源详情")
def get_resource(rid: int, db: Session = Depends(get_db)):
    r = db.query(ResourceItem).filter(ResourceItem.id == rid).first()
    if not r:
        raise HTTPException(404, "资源不存在")
    return to_dict(r)


# ---------------- 写入（仅管理员） ----------------
@router.post("/resources", summary="新建资源", status_code=201, dependencies=[Depends(require_admin)])
def create_resource(data: ResourceCreate, db: Session = Depends(get_db),
                    admin=Depends(require_admin)):
    r = ResourceItem(
        name=data.name, pve_ip=data.pve_ip, vm_ip=data.vm_ip, domain=data.domain,
        owner=data.owner, projects=data.projects, machine_type=data.machine_type,
        category=data.category or "vm",
        location=data.location, office_area=data.office_area,
        expire_at=data.expire_at, alert_count=data.alert_count,
        status=data.status, remark=data.remark, account=data.account,
        password=encrypt_password(data.password) if data.password else "",
        machine_id=data.machine_id,
        cabinet_id=data.cabinet_id, ru_position=data.ru_position,
        ru_height=data.ru_height if data.ru_height else 1,
        creator=admin["username"], followers=[], comments=[],
        timeline=[{"id": f"t{datetime.now().timestamp():.0f}", "user": admin["username"],
                   "action": "创建资源", "time": now_short()}],
    )
    db.add(r); db.commit(); db.refresh(r)
    return to_dict(r)


@router.put("/resources/{rid}", summary="编辑资源", dependencies=[Depends(require_admin)])
def update_resource(rid: int, data: ResourceUpdate, db: Session = Depends(get_db),
                    admin=Depends(require_admin)):
    r = db.query(ResourceItem).filter(ResourceItem.id == rid).first()
    if not r:
        raise HTTPException(404, "资源不存在")
    d = data.model_dump(exclude_unset=True)
    nullable = {"cabinet_id", "ru_position"}
    for field, val in d.items():
        if val is None and field not in nullable:
            continue
        if field == "password":
            val = encrypt_password(val) if val else ""
        setattr(r, field, val)
    if "cabinet_id" in d and d.get("cabinet_id"):
        cab = db.query(Cabinet).filter(Cabinet.id == d["cabinet_id"]).first()
        if cab:
            r.location = f"{cab.room_name}-{cab.cabinet_name}" if cab.room_name else cab.cabinet_name
    if d.get("password"):
        r.timeline.append({"id": f"t{datetime.now().timestamp():.0f}", "user": admin["username"],
                            "action": "修改登录密码", "time": now_short()})
    r.timeline.append({"id": f"t{datetime.now().timestamp():.0f}", "user": admin["username"],
                        "action": "编辑资源", "time": now_short()})
    flag_modified(r, "timeline")
    r.updated_at = datetime.now()
    db.commit(); db.refresh(r)
    return to_dict(r)


@router.delete("/resources/{rid}", summary="删除资源", dependencies=[Depends(require_admin)])
def delete_resource(rid: int, db: Session = Depends(get_db)):
    r = db.query(ResourceItem).filter(ResourceItem.id == rid).first()
    if not r:
        raise HTTPException(404, "资源不存在")
    db.delete(r); db.commit()
    return {"ok": True, "deleted": rid}


@router.post("/resources/{rid}/transfer", summary="转交负责人", dependencies=[Depends(require_admin)])
def transfer_resource(rid: int, body: TransferBody, db: Session = Depends(get_db),
                      admin=Depends(require_admin)):
    r = db.query(ResourceItem).filter(ResourceItem.id == rid).first()
    if not r:
        raise HTTPException(404, "资源不存在")
    old = r.owner
    r.owner = body.owner
    r.timeline.append({"id": f"t{datetime.now().timestamp():.0f}", "user": admin["username"],
                        "action": f"转交负责人 {old} → {body.owner}", "time": now_short()})
    flag_modified(r, "timeline")
    r.updated_at = datetime.now()
    db.commit(); db.refresh(r)
    return to_dict(r)


# ---------------- 协作（任意登录用户） ----------------
@router.post("/resources/{rid}/comment", summary="发表评论")
def comment_resource(rid: int, body: CommentBody, db: Session = Depends(get_db),
                     user=Depends(get_current_user)):
    r = db.query(ResourceItem).filter(ResourceItem.id == rid).first()
    if not r:
        raise HTTPException(404, "资源不存在")
    r.comments.insert(0, {
        "id": f"c{datetime.now().timestamp():.0f}",
        "user": user["username"],
        "text": body.text,
        "time": now_short(),
    })
    r.timeline.append({"id": f"t{datetime.now().timestamp():.0f}", "user": user["username"],
                        "action": "发表评论", "time": now_short()})
    flag_modified(r, "comments")
    flag_modified(r, "timeline")
    r.updated_at = datetime.now()
    db.commit(); db.refresh(r)
    return to_dict(r)


@router.post("/resources/{rid}/follow", summary="关注/取消关注")
def follow_resource(rid: int, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    r = db.query(ResourceItem).filter(ResourceItem.id == rid).first()
    if not r:
        raise HTTPException(404, "资源不存在")
    followers = r.followers or []
    if user["username"] in followers:
        followers.remove(user["username"])
        action = "取消关注"
    else:
        followers.append(user["username"])
        action = "关注资源"
    r.followers = followers
    r.timeline.append({"id": f"t{datetime.now().timestamp():.0f}", "user": user["username"],
                        "action": action, "time": now_short()})
    flag_modified(r, "timeline")
    r.updated_at = datetime.now()
    db.commit(); db.refresh(r)
    return to_dict(r)


# ---------------- 种子（仅管理员，表空时插入演示数据） ----------------
SEED = [
    {"name": "web-prod-01", "pve_ip": "192.168.1.10", "vm_ip": "10.20.1.11", "domain": "app.example.com",
     "owner": "张伟(运维)", "projects": ["核心交易", "官网"], "machine_type": "服务器", "category": "physical", "location": "A栋-12柜",
     "expire_at": "2026-12-31", "alert_count": 0, "status": "online", "remark": "生产 web 节点",
     "followers": ["张伟(运维)"],
     "comments": [{"id": "c1", "user": "李娜(开发)", "text": "这台机器 @张伟 帮忙看下域名解析", "time": "07-29 11:00"}],
     "timeline": [{"id": "t1", "user": "张伟(运维)", "action": "创建资源", "time": "07-20 09:00"}]},
    {"name": "db-master", "pve_ip": "192.168.1.10", "vm_ip": "10.20.1.20", "domain": "db.example.com",
     "owner": "王强(架构)", "projects": ["核心交易"], "machine_type": "KVM 虚拟机", "category": "vm", "location": "A栋-13柜",
     "expire_at": "2026-08-15", "alert_count": 2, "status": "online", "remark": "主库", "followers": [],
     "comments": [], "timeline": [{"id": "t1", "user": "王强(架构)", "action": "创建资源", "time": "07-18 14:00"}]},
    {"name": "pve-node-01", "pve_ip": "192.168.1.10", "vm_ip": "-", "domain": "-",
     "owner": "张伟(运维)", "projects": ["基础设施"], "machine_type": "PVE 宿主机", "category": "physical", "location": "A栋-10柜",
     "expire_at": "2027-01-01", "alert_count": 0, "status": "online", "remark": "PVE 集群主节点",
     "followers": ["张伟(运维)"], "comments": [],
     "timeline": [{"id": "t1", "user": "张伟(运维)", "action": "创建资源", "time": "07-10 10:00"}]},
    {"name": "test-vm-07", "pve_ip": "192.168.1.12", "vm_ip": "10.20.2.7", "domain": "test7.example.com",
     "owner": "赵敏(测试)", "projects": ["回归测试"], "machine_type": "VMware", "category": "vm", "location": "B栋-05柜",
     "expire_at": "2026-08-05", "alert_count": 0, "status": "offline", "remark": "测试环境", "followers": [],
     "comments": [], "timeline": [{"id": "t1", "user": "赵敏(测试)", "action": "创建资源", "time": "07-12 11:00"}]},
    {"name": "cache-redis", "pve_ip": "192.168.1.10", "vm_ip": "10.20.1.30", "domain": "cache.example.com",
     "owner": "王强(架构)", "projects": ["核心交易", "活动平台"], "machine_type": "容器", "category": "vm", "location": "A栋-14柜",
     "expire_at": "2026-11-20", "alert_count": 1, "status": "online", "remark": "Redis 集群", "followers": [],
     "comments": [], "timeline": [{"id": "t1", "user": "王强(架构)", "action": "创建资源", "time": "07-15 13:00"}]},
    {"name": "pve-node-02", "pve_ip": "192.168.1.12", "vm_ip": "-", "domain": "-",
     "owner": "张伟(运维)", "projects": ["基础设施"], "machine_type": "PVE 宿主机", "category": "physical", "location": "B栋-02柜",
     "expire_at": "2027-01-01", "alert_count": 0, "status": "maintenance", "remark": "维护中", "followers": [],
     "comments": [], "timeline": [{"id": "t1", "user": "张伟(运维)", "action": "创建资源", "time": "07-10 10:30"}]},
]


@router.post("/seed", summary="种子演示数据（表空时插入）", dependencies=[Depends(require_admin)])
def seed_resources(db: Session = Depends(get_db), admin=Depends(require_admin)):
    cnt = db.query(func.count(ResourceItem.id)).scalar() or 0
    if cnt > 0:
        return {"ok": True, "skipped": True, "message": f"已有 {cnt} 条，跳过种子", "count": cnt}
    for s in SEED:
        r = ResourceItem(
            name=s["name"], pve_ip=s["pve_ip"], vm_ip=s["vm_ip"], domain=s["domain"],
            owner=s["owner"], projects=s["projects"], machine_type=s["machine_type"],
            location=s["location"], office_area=s.get("office_area", ""),
            expire_at=s["expire_at"], alert_count=s["alert_count"],
            status=s["status"], remark=s["remark"], followers=s.get("followers", []),
            comments=s.get("comments", []), timeline=s.get("timeline", []),
            creator=admin["username"],
        )
        db.add(r)
    db.commit()
    return {"ok": True, "seeded": len(SEED), "count": len(SEED)}




# ---------------- 资源选项管理（PVE主机 / 项目 / 物理位置） ----------------
import json, os

OPTIONS_FILE = '/app/data/resource_options.json'
DEFAULT_OPTIONS = {
    'pve_hosts': ['192.168.1.10', '192.168.1.12'],
    'projects': ['核心交易', '官网', '基础设施', '回归测试', '活动平台', '前端服务', '测试', '缓存'],
    'locations': ['A栋-10柜', 'A栋-12柜', 'A栋-13柜', 'A栋-14柜', 'B栋-02柜', 'B栋-05柜'],
    'machine_types': [], 'owners': [],
    'machine_exclude': [],
    'bindings': {
        'pve_location': [
            {'host': '192.168.1.10', 'location': 'A栋-10柜'},
            {'host': '192.168.1.12', 'location': 'B栋-02柜'}
        ],
        'project_ip': [
            {'project': '核心交易', 'ip': '192.168.1.10'},
            {'project': '官网', 'ip': '192.168.1.10'}
        ]
    }
}

def _load_options() -> dict:
    try:
        with open(OPTIONS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 补全缺失 key
            for k in ('pve_hosts', 'projects', 'locations', 'machine_types', 'owners', 'machine_exclude', 'bindings'):
                if k not in data:
                    data[k] = DEFAULT_OPTIONS[k]
            if 'bindings' in data:
                for bk in ('pve_location', 'project_ip'):
                    if bk not in data['bindings']:
                        data['bindings'][bk] = DEFAULT_OPTIONS['bindings'][bk]
            return data
    except:
        return DEFAULT_OPTIONS.copy()

def _save_options(opts: dict):
    os.makedirs(os.path.dirname(OPTIONS_FILE), exist_ok=True)
    with open(OPTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(opts, f, ensure_ascii=False, indent=2)

@router.get('/options', summary='获取可选项列表+绑定')
def get_options():
    opts = _load_options()
    # 补充推导字段
    bindings = opts.get('bindings', {})
    hosts_by_loc = {}
    for b in bindings.get('pve_location', []):
        hosts_by_loc.setdefault(b['location'], []).append(b['host'])
    ip_by_project = {}
    for b in bindings.get('project_ip', []):
        ip_by_project[b['project']] = b['ip']
    return {
        'ok': True,
        'data': opts,
        'hosts_by_location': hosts_by_loc,
        'ip_by_project': ip_by_project,
    }

class OptionsBody(BaseModel):
    pve_hosts: Optional[List[str]] = None
    projects: Optional[List[str]] = None
    locations: Optional[List[str]] = None
    machine_types: Optional[List[str]] = None
    owners: Optional[List[str]] = None
    machine_exclude: Optional[List[int]] = None
    bindings: Optional[dict] = None

@router.put('/options', summary='更新可选项（仅管理员）', dependencies=[Depends(require_admin)])
def update_options(body: OptionsBody):
    opts = _load_options()
    for key in ('pve_hosts', 'projects', 'locations', 'machine_types', 'owners', 'machine_exclude'):
        val = getattr(body, key, None)
        if val is not None:
            opts[key] = val
    if body.bindings is not None:
        opts['bindings'] = body.bindings
    _save_options(opts)
    return {'ok': True, 'data': opts}


# ---------------- 机房 / 机柜（U 位视图） ----------------
def cabinet_to_dict(c: Cabinet, devices=None) -> dict:
    devices = devices or []
    return {
        "id": c.id,
        "room_name": c.room_name or "",
        "cabinet_name": c.cabinet_name or "",
        "ru_total": c.ru_total or 47,
        "power_low": c.power_low or "",
        "power_high": c.power_high or "",
        "remark": c.remark or "",
        "sort_order": c.sort_order or 0,
        "used_ru": sum((d.ru_height or 1) for d in devices),
        "devices": [{
            "id": d.id, "name": d.name, "pve_ip": d.pve_ip or "", "vm_ip": d.vm_ip or "",
            "machine_type": d.machine_type or "", "status": d.status or "online",
            "owner": d.owner or "", "remark": d.remark or "",
            "ru_position": d.ru_position, "ru_height": d.ru_height or 1,
        } for d in devices],
    }


class CabinetCreate(BaseModel):
    room_name: str = ""
    cabinet_name: str = Field(..., min_length=1, max_length=128)
    ru_total: int = 47
    power_low: str = ""
    power_high: str = ""
    remark: str = ""
    sort_order: int = 0


class CabinetUpdate(BaseModel):
    room_name: Optional[str] = None
    cabinet_name: Optional[str] = None
    ru_total: Optional[int] = None
    power_low: Optional[str] = None
    power_high: Optional[str] = None
    remark: Optional[str] = None
    sort_order: Optional[int] = None


class PlaceBody(BaseModel):
    resource_id: int
    cabinet_id: Optional[int] = None
    ru_position: Optional[int] = None
    ru_height: int = 1


class UnplaceBody(BaseModel):
    resource_id: int


@router.get("/cabinets", summary="列出机柜及其上架设备（仅物理机）")
def list_cabinets(db: Session = Depends(get_db)):
    cabs = db.query(Cabinet).order_by(Cabinet.sort_order, Cabinet.id).all()
    rows = db.query(ResourceItem).filter(
        ResourceItem.cabinet_id.isnot(None),
        ResourceItem.category == "physical",
    ).all()
    by_cab = {}
    for r in rows:
        if r.ru_position is None:
            continue
        by_cab.setdefault(r.cabinet_id, []).append(r)
    return [cabinet_to_dict(c, by_cab.get(c.id, [])) for c in cabs]


@router.post("/cabinets", summary="新建机柜", status_code=201, dependencies=[Depends(require_admin)])
def create_cabinet(data: CabinetCreate, db: Session = Depends(get_db),
                   admin=Depends(require_admin)):
    c = Cabinet(room_name=data.room_name, cabinet_name=data.cabinet_name,
                ru_total=data.ru_total or 47, power_low=data.power_low,
                power_high=data.power_high, remark=data.remark,
                sort_order=data.sort_order, creator=admin["username"])
    db.add(c); db.commit(); db.refresh(c)
    return cabinet_to_dict(c, [])


@router.put("/cabinets/{cid}", summary="编辑机柜", dependencies=[Depends(require_admin)])
def update_cabinet(cid: int, data: CabinetUpdate, db: Session = Depends(get_db)):
    c = db.query(Cabinet).filter(Cabinet.id == cid).first()
    if not c:
        raise HTTPException(404, "机柜不存在")
    for field, val in data.model_dump(exclude_unset=True).items():
        if val is None:
            continue
        setattr(c, field, val)
    c.updated_at = datetime.now()
    db.commit(); db.refresh(c)
    return cabinet_to_dict(c, [])


@router.delete("/cabinets/{cid}", summary="删除机柜（其下设备自动下架）",
               dependencies=[Depends(require_admin)])
def delete_cabinet(cid: int, db: Session = Depends(get_db)):
    c = db.query(Cabinet).filter(Cabinet.id == cid).first()
    if not c:
        raise HTTPException(404, "机柜不存在")
    db.query(ResourceItem).filter(ResourceItem.cabinet_id == cid).update(
        {"cabinet_id": None, "ru_position": None})
    db.delete(c); db.commit()
    return {"ok": True, "deleted": cid}


@router.post("/cabinets/place", summary="上架 / 移动设备到指定机柜 U 位",
             dependencies=[Depends(require_admin)])
def place_resource(body: PlaceBody, db: Session = Depends(get_db),
                   admin=Depends(require_admin)):
    r = db.query(ResourceItem).filter(ResourceItem.id == body.resource_id).first()
    if not r:
        raise HTTPException(404, "资源不存在")
    if body.cabinet_id is None:
        r.cabinet_id = None
        r.ru_position = None
        action = "从机柜下架"
    else:
        cab = db.query(Cabinet).filter(Cabinet.id == body.cabinet_id).first()
        if not cab:
            raise HTTPException(404, "机柜不存在")
        pos = body.ru_position or 1
        h = max(1, body.ru_height or 1)
        total = cab.ru_total or 47
        if pos < 1 or pos + h - 1 > total:
            raise HTTPException(400, f"U 位超出范围（该柜共 {total}U）")
        others = db.query(ResourceItem).filter(
            ResourceItem.cabinet_id == cab.id,
            ResourceItem.id != r.id,
            ResourceItem.ru_position.isnot(None)).all()
        for o in others:
            oh = o.ru_height or 1
            if pos < o.ru_position + oh and o.ru_position < pos + h:
                raise HTTPException(409, f"U{pos}~U{pos + h - 1} 与 {o.name}（U{o.ru_position}）冲突")
        r.cabinet_id = cab.id
        r.ru_position = pos
        r.ru_height = h
        r.location = f"{cab.room_name}-{cab.cabinet_name}" if cab.room_name else cab.cabinet_name
        action = f"上架到 {r.location} U{pos}"
    r.timeline.append({"id": f"t{datetime.now().timestamp():.0f}", "user": admin["username"],
                        "action": action, "time": now_short()})
    flag_modified(r, "timeline")
    r.updated_at = datetime.now()
    db.commit(); db.refresh(r)
    return to_dict(r)


@router.post("/cabinets/unplace", summary="设备下架", dependencies=[Depends(require_admin)])
def unplace_resource(body: UnplaceBody, db: Session = Depends(get_db),
                     admin=Depends(require_admin)):
    r = db.query(ResourceItem).filter(ResourceItem.id == body.resource_id).first()
    if not r:
        raise HTTPException(404, "资源不存在")
    r.cabinet_id = None
    r.ru_position = None
    r.timeline.append({"id": f"t{datetime.now().timestamp():.0f}", "user": admin["username"],
                        "action": "从机柜下架", "time": now_short()})
    flag_modified(r, "timeline")
    r.updated_at = datetime.now()
    db.commit(); db.refresh(r)
    return to_dict(r)


def _split_location(loc: str):
    for sep in ("-", "－", "—", "–"):
        if sep in loc:
            a, b = loc.split(sep, 1)
            return a.strip(), b.strip()
    return "", loc.strip()


@router.post("/cabinets/migrate-from-locations", summary="从现有 location 字段生成机柜并回填",
             dependencies=[Depends(require_admin)])
def migrate_from_locations(db: Session = Depends(get_db), admin=Depends(require_admin)):
    rows = db.query(ResourceItem).filter(
        ResourceItem.location.isnot(None), ResourceItem.location != "").all()
    cache = {}
    created = 0
    linked = 0
    for r in rows:
        if r.cabinet_id:
            continue
        loc = (r.location or "").strip()
        if not loc:
            continue
        cab = cache.get(loc)
        if not cab:
            cab = db.query(Cabinet).filter(Cabinet.cabinet_name == loc).first()
            if not cab:
                room, cabname = _split_location(loc)
                cab = Cabinet(room_name=room, cabinet_name=cabname or loc,
                              ru_total=47, creator=admin["username"])
                db.add(cab)
                db.flush()
                created += 1
            cache[loc] = cab
        r.cabinet_id = cab.id
        linked += 1
    db.commit()
    return {"ok": True, "created": created, "linked": linked}
