"""资源管理台账模型（与蓝鲸 CMDB 完全隔离，独立新表 resource_items）
记录 PVE 宿主机 / 虚拟机 / 域名的归属与协作：负责人、项目、机房机柜、到期、告警，
以及协作数据（关注人、评论、变更时间线）。评论/时间线/关注人用 JSON 列存储，
与前端既有数据结构保持一致，降低迁移成本。
仅新增表，不修改任何现有表/字段。
"""
from sqlalchemy import (
    Column, Integer, String, Text, JSON, DateTime,
)
from datetime import datetime
from models.database import Base


class ResourceItem(Base):
    """资源管理台账 — 单条资源记录"""
    __tablename__ = "resource_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, comment="资源名称")
    pve_ip = Column(String(45), default="", comment="PVE 宿主机 IP")
    vm_ip = Column(String(45), default="", comment="虚拟机 IP")
    domain = Column(String(255), default="", comment="域名")
    owner = Column(String(128), default="", comment="负责人")
    projects = Column(JSON, default=list, comment="关联项目列表")
    machine_type = Column(String(64), default="", comment="机器类型")
    category = Column(String(16), default="vm", comment="资源分类 physical=物理机 / vm=虚拟机")
    location = Column(String(255), default="", comment="机房/机柜")
    office_area = Column(String(64), default="", comment="办公区，如 南办公区/北办公区")
    expire_at = Column(String(32), default="", comment="到期时间 YYYY-MM-DD")
    alert_count = Column(Integer, default=0, comment="关联告警数")
    status = Column(String(32), default="online", comment="状态 online/offline/maintenance")
    remark = Column(Text, default="", comment="备注")
    account = Column(String(128), default="", comment="登录账号")
    password = Column(String(512), default="", comment="登录密码(AES加密)")
    machine_id = Column(Integer, nullable=True, default=None, comment="关联监控中心机器ID(从监控中心导入)")
    cabinet_id = Column(Integer, nullable=True, default=None, comment="所属机柜ID(cabinets.id)")
    ru_position = Column(Integer, nullable=True, default=None, comment="起始U位(自底向上，1 起)")
    ru_height = Column(Integer, default=1, comment="占用U高度，默认1U")
    # 协作数据（JSON 列，结构与前端既有 localStorage 数据一致）
    followers = Column(JSON, default=list, comment="关注人用户名列表")
    comments = Column(JSON, default=list, comment="评论列表 [{id,user,text,time}]")
    timeline = Column(JSON, default=list, comment="变更时间线 [{id,user,action,time}]")
    creator = Column(String(128), default="admin", comment="创建人")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class Cabinet(Base):
    """机房 / 机柜 — 物理机柜台账，U 位视图的骨架"""
    __tablename__ = "cabinets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_name = Column(String(128), default="", comment="机房名称，如 A 栋机房")
    cabinet_name = Column(String(128), default="", comment="机柜编号，如 A-01")
    ru_total = Column(Integer, default=47, comment="机柜总U位数")
    power_low = Column(String(32), default="", comment="电源下限 A")
    power_high = Column(String(32), default="", comment="电源上限 A")
    remark = Column(Text, default="", comment="备注")
    sort_order = Column(Integer, default=0, comment="排序号")
    creator = Column(String(128), default="admin", comment="创建人")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
