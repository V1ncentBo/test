"""CMDB 路由（蓝鲸「模型」+「资源」模块移植 - FastAPI 版）
提供：模型管理(分类/模型/属性/关联类型)、资源实例(CRUD+关联)、主线拓扑读取。
所有端点挂在 /api/cmdb 下，与现有 /api/machines 等完全隔离，不影响既有功能。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime

from models.database import get_db
from routers.auth import get_current_user

router = APIRouter(
    prefix="/api/cmdb",
    tags=["CMDB 模型与资源"],
    dependencies=[Depends(get_current_user)],
)


# ---------------- Pydantic Schema ----------------
class ClassificationCreate(BaseModel):
    bk_classification_id: str
    name: str
    icon: str = ""
    description: str = ""
    order: int = 0


class ModelCreate(BaseModel):
    bk_obj_id: str
    name: str
    bk_classification_id: str
    description: str = ""
    ispreset: bool = False


class AttributeCreate(BaseModel):
    bk_property_id: str
    bk_property_name: str
    bk_property_type: str = "singlechar"
    bk_property_group: str = "default"
    isrequired: bool = False
    isreadonly: bool = False
    option: List[Any] = Field(default_factory=list)
    description: str = ""
    editor: str = "input"


class InstanceCreate(BaseModel):
    bk_obj_id: str
    bk_inst_id: int
    attributes: dict = Field(default_factory=dict)
    bk_supplier_account: str = "0"


class InstanceAssociationCreate(BaseModel):
    bk_asst_id: str
    to_bk_obj_id: str
    to_bk_inst_id: int


class BizCreate(BaseModel):
    bk_biz_id: int
    name: str
    description: str = ""


class SetCreate(BaseModel):
    bk_set_id: int
    bk_biz_id: int
    name: str


class ModuleCreate(BaseModel):
    bk_module_id: int
    bk_set_id: int
    name: str


class ModuleHostCreate(BaseModel):
    bk_module_id: int
    machine_id: int
    bk_inst_id: Optional[int] = None


# ---------------- 导入模型（避免循环导入，本地导入） ----------------
from models.cmdb import CmdbClassification, CmdbModel, CmdbModelAttribute, CmdbAssociationType, CmdbInstance, CmdbInstanceAssociation, CmdbBusiness, CmdbSet, CmdbModule, CmdbModuleHost


# ================= 分类 =================
@router.get("/classifications", summary="列出所有模型分组")
def list_classifications(db: Session = Depends(get_db)):
    return db.query(CmdbClassification).order_by(CmdbClassification.order).all()


@router.post("/classifications", summary="新建模型分组", status_code=201)
def create_classification(data: ClassificationCreate, db: Session = Depends(get_db)):
    if db.query(CmdbClassification).filter_by(bk_classification_id=data.bk_classification_id).first():
        raise HTTPException(409, "分组ID已存在")
    obj = CmdbClassification(**data.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


# ================= 模型 =================
@router.get("/models", summary="列出所有模型")
def list_models(db: Session = Depends(get_db)):
    return db.query(CmdbModel).all()


@router.post("/models", summary="新建模型", status_code=201)
def create_model(data: ModelCreate, db: Session = Depends(get_db)):
    if db.query(CmdbModel).filter_by(bk_obj_id=data.bk_obj_id).first():
        raise HTTPException(409, "模型ID已存在")
    obj = CmdbModel(**data.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@router.get("/models/{bk_obj_id}", summary="获取单个模型")
def get_model(bk_obj_id: str, db: Session = Depends(get_db)):
    obj = db.query(CmdbModel).filter_by(bk_obj_id=bk_obj_id).first()
    if not obj:
        raise HTTPException(404, "模型不存在")
    return obj


# ================= 模型属性 =================
@router.get("/models/{bk_obj_id}/attributes", summary="列出模型属性")
def list_attributes(bk_obj_id: str, db: Session = Depends(get_db)):
    return db.query(CmdbModelAttribute).filter_by(bk_obj_id=bk_obj_id).all()


@router.post("/models/{bk_obj_id}/attributes", summary="为模型添加属性", status_code=201)
def create_attribute(bk_obj_id: str, data: AttributeCreate, db: Session = Depends(get_db)):
    if not db.query(CmdbModel).filter_by(bk_obj_id=bk_obj_id).first():
        raise HTTPException(404, "模型不存在")
    obj = CmdbModelAttribute(bk_obj_id=bk_obj_id, **data.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


# ================= 关联类型 =================
@router.get("/association-types", summary="列出关联类型")
def list_asst_types(db: Session = Depends(get_db)):
    return db.query(CmdbAssociationType).all()


@router.post("/association-types", summary="新建关联类型", status_code=201)
def create_asst_type(data: dict, db: Session = Depends(get_db)):
    obj = CmdbAssociationType(
        bk_asst_id=data.get("bk_asst_id"),
        bk_asst_name=data.get("bk_asst_name", ""),
        description=data.get("description", ""),
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


# ================= 资源实例 =================
@router.get("/instances", summary="按模型列出实例")
def list_instances(bk_obj_id: str, db: Session = Depends(get_db)):
    return db.query(CmdbInstance).filter_by(bk_obj_id=bk_obj_id).all()


@router.post("/instances", summary="新建资源实例", status_code=201)
def create_instance(data: InstanceCreate, db: Session = Depends(get_db)):
    if not db.query(CmdbModel).filter_by(bk_obj_id=data.bk_obj_id).first():
        raise HTTPException(404, "模型不存在")
    if db.query(CmdbInstance).filter_by(bk_obj_id=data.bk_obj_id, bk_inst_id=data.bk_inst_id).first():
        raise HTTPException(409, "该模型下实例ID已存在")
    obj = CmdbInstance(**data.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@router.get("/instances/{inst_pk}", summary="获取单个实例")
def get_instance(inst_pk: int, db: Session = Depends(get_db)):
    obj = db.query(CmdbInstance).filter_by(id=inst_pk).first()
    if not obj:
        raise HTTPException(404, "实例不存在")
    return obj


@router.put("/instances/{inst_pk}", summary="更新实例动态属性")
def update_instance(inst_pk: int, data: dict, db: Session = Depends(get_db)):
    obj = db.query(CmdbInstance).filter_by(id=inst_pk).first()
    if not obj:
        raise HTTPException(404, "实例不存在")
    if "attributes" in data:
        obj.attributes = data["attributes"]
    obj.last_time = datetime.now()
    db.commit(); db.refresh(obj)
    return obj


@router.post("/instances/{inst_pk}/associations", summary="为实例添加关联", status_code=201)
def create_instance_association(inst_pk: int, data: InstanceAssociationCreate, db: Session = Depends(get_db)):
    obj = db.query(CmdbInstance).filter_by(id=inst_pk).first()
    if not obj:
        raise HTTPException(404, "源实例不存在")
    link = CmdbInstanceAssociation(
        bk_obj_id=obj.bk_obj_id, bk_inst_id=obj.bk_inst_id,
        bk_asst_id=data.bk_asst_id, to_bk_obj_id=data.to_bk_obj_id,
        to_bk_inst_id=data.to_bk_inst_id,
    )
    db.add(link); db.commit(); db.refresh(link)
    return link


# ================= 主线拓扑 =================
@router.post("/business", summary="新建业务", status_code=201)
def create_business(data: BizCreate, db: Session = Depends(get_db)):
    obj = CmdbBusiness(**data.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@router.post("/set", summary="新建集群", status_code=201)
def create_set(data: SetCreate, db: Session = Depends(get_db)):
    obj = CmdbSet(**data.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@router.post("/module", summary="新建模块", status_code=201)
def create_module(data: ModuleCreate, db: Session = Depends(get_db)):
    obj = CmdbModule(**data.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@router.post("/module-host", summary="模块挂载主机", status_code=201)
def create_module_host(data: ModuleHostCreate, db: Session = Depends(get_db)):
    obj = CmdbModuleHost(**data.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@router.get("/topology", summary="读取主线拓扑树(业务>集群>模块>主机)")
def get_topology(db: Session = Depends(get_db)):
    """返回 业务->集群->模块->主机的层级关系，主机关联 machine_info 名称/IP。"""
    from models.schema import MachineInfo
    bizs = db.query(CmdbBusiness).all()
    result = []
    for b in bizs:
        sets = db.query(CmdbSet).filter_by(bk_biz_id=b.bk_biz_id).all()
        set_list = []
        for s in sets:
            mods = db.query(CmdbModule).filter_by(bk_set_id=s.bk_set_id).all()
            mod_list = []
            for m in mods:
                hosts = (
                    db.query(CmdbModuleHost, MachineInfo)
                    .join(MachineInfo, CmdbModuleHost.machine_id == MachineInfo.id, isouter=True)
                    .filter(CmdbModuleHost.bk_module_id == m.bk_module_id)
                    .all()
                )
                mod_list.append({
                    "bk_module_id": m.bk_module_id,
                    "name": m.name,
                    "hosts": [
                        {"machine_id": h.CmdbModuleHost.machine_id,
                         "name": (h.MachineInfo.name if h.MachineInfo else None),
                         "ip": (h.MachineInfo.ip if h.MachineInfo else None)}
                        for h in hosts
                    ],
                })
            set_list.append({"bk_set_id": s.bk_set_id, "name": s.name, "modules": mod_list})
        result.append({"bk_biz_id": b.bk_biz_id, "name": b.name, "sets": set_list})
    return result
