"""CMDB 元数据驱动模型（蓝鲸「模型」+「资源」模块移植 - FastAPI/SQLAlchemy 版）
设计源自 bkcmdb-port-analysis.md 第5节，表名 cmdb_* 前缀，与现有 machine_info 完全隔离。
仅新增表，不修改任何现有表/字段。
"""
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Text, Boolean, JSON,
    ForeignKey, UniqueConstraint,
)
from datetime import datetime
from models.database import Base


class CmdbClassification(Base):
    """模型分组（模型管理的分类树节点）"""
    __tablename__ = "cmdb_classifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bk_classification_id = Column(String(64), unique=True, nullable=False, comment="分组ID")
    name = Column(String(128), nullable=False, comment="分组名称")
    icon = Column(String(64), default="", comment="图标")
    description = Column(String(255), default="", comment="描述")
    order = Column(Integer, default=0, comment="排序")


class CmdbModel(Base):
    """模型（对应蓝鲸的 Object/模型，如 host/switch/business 等）"""
    __tablename__ = "cmdb_models"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bk_obj_id = Column(String(64), unique=True, nullable=False, comment="模型ID,如 host/switch")
    name = Column(String(128), nullable=False, comment="模型名称")
    bk_classification_id = Column(String(64), nullable=False, comment="所属分组", index=True)
    description = Column(String(255), default="", comment="描述")
    ispreset = Column(Boolean, default=False, comment="是否内置模型")
    creator = Column(String(64), default="admin", comment="创建人")
    create_time = Column(DateTime, default=datetime.now, comment="创建时间")


class CmdbModelAttribute(Base):
    """模型属性（元数据三元组之一：模型 -> 属性）"""
    __tablename__ = "cmdb_model_attributes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bk_obj_id = Column(String(64), nullable=False, comment="所属模型", index=True)
    bk_property_id = Column(String(64), nullable=False, comment="属性ID")
    bk_property_name = Column(String(128), nullable=False, comment="属性名称")
    bk_property_type = Column(String(32), default="singlechar",
                              comment="类型:singlechar/int/enum/date/datetime/bool/longchar/float")
    bk_property_group = Column(String(64), default="default", comment="属性分组")
    isrequired = Column(Boolean, default=False, comment="是否必填")
    isreadonly = Column(Boolean, default=False, comment="是否只读")
    option = Column(JSON, default=list, comment="枚举选项列表")
    description = Column(String(255), default="", comment="描述")
    editor = Column(String(32), default="input", comment="编辑器类型")
    __table_args__ = (UniqueConstraint('bk_obj_id', 'bk_property_id', name='uk_obj_prop'),)


class CmdbAssociationType(Base):
    """关联类型（模型间关联的定义，如 属于/包含/运行于）"""
    __tablename__ = "cmdb_association_types"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bk_asst_id = Column(String(64), unique=True, nullable=False, comment="关联类型ID")
    bk_asst_name = Column(String(128), nullable=False, comment="关联名称")
    description = Column(String(255), default="", comment="描述")


class CmdbModelAssociation(Base):
    """模型间关联（拓扑关系定义）"""
    __tablename__ = "cmdb_model_associations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bk_obj_id = Column(String(64), nullable=False, comment="源模型", index=True)
    bk_asst_id = Column(String(64), nullable=False, comment="关联类型")
    to_bk_obj_id = Column(String(64), nullable=False, comment="目标模型", index=True)


class CmdbInstance(Base):
    """通用资源实例（动态 schema：所有属性存于 attributes JSON 列）
    对应蓝鲸的 Instance。元数据驱动：同一张表承载所有模型的实例。"""
    __tablename__ = "cmdb_instances"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bk_obj_id = Column(String(64), nullable=False, comment="所属模型", index=True)
    bk_inst_id = Column(Integer, nullable=False, comment="实例ID(模型内唯一)")
    bk_supplier_account = Column(String(64), default="0", comment="供应商账号")
    creator = Column(String(64), default="admin", comment="创建人")
    attributes = Column(JSON, default=dict, comment="动态属性键值对")
    create_time = Column(DateTime, default=datetime.now, comment="创建时间")
    last_time = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    __table_args__ = (UniqueConstraint('bk_obj_id', 'bk_inst_id', name='uk_obj_inst'),)


class CmdbInstanceAssociation(Base):
    """实例间关联（资源拓扑的边）"""
    __tablename__ = "cmdb_instance_associations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bk_obj_id = Column(String(64), nullable=False, comment="源模型", index=True)
    bk_inst_id = Column(Integer, nullable=False, comment="源实例")
    bk_asst_id = Column(String(64), nullable=False, comment="关联类型")
    to_bk_obj_id = Column(String(64), nullable=False, comment="目标模型")
    to_bk_inst_id = Column(Integer, nullable=False, comment="目标实例")


class CmdbBusiness(Base):
    """主线拓扑：业务"""
    __tablename__ = "cmdb_business"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bk_biz_id = Column(Integer, unique=True, nullable=False, comment="业务ID")
    name = Column(String(128), nullable=False, comment="业务名称")
    description = Column(String(255), default="", comment="描述")


class CmdbSet(Base):
    """主线拓扑：集群(Set)"""
    __tablename__ = "cmdb_set"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bk_set_id = Column(Integer, nullable=False, comment="集群ID")
    bk_biz_id = Column(Integer, nullable=False, comment="所属业务", index=True)
    name = Column(String(128), nullable=False, comment="集群名称")


class CmdbModule(Base):
    """主线拓扑：模块(Module)"""
    __tablename__ = "cmdb_module"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bk_module_id = Column(Integer, nullable=False, comment="模块ID")
    bk_set_id = Column(Integer, nullable=False, comment="所属集群", index=True)
    name = Column(String(128), nullable=False, comment="模块名称")


class CmdbModuleHost(Base):
    """主线拓扑：模块与主机(machine_info)的挂载关系"""
    __tablename__ = "cmdb_module_host"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bk_module_id = Column(Integer, nullable=False, comment="模块ID", index=True)
    machine_id = Column(Integer, nullable=False, comment="关联 machine_info.id", index=True)
    bk_inst_id = Column(Integer, nullable=True, comment="关联 cmdb_instances.id")
