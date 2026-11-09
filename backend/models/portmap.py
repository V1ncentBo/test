"""工位网口图（PortMap）模型 —— 独立新表，不修改任何现有表 / 字段。

设计要点
--------
· portmaps          一张平面图（楼层 / 区域）。承载行列数、格子像素尺寸、全局「外显」字段。
· portmap_cells     图上的一个格子。kind 区分 网口 / 过道 / 功能区 / 柜体 / 编号 等；
                    只有网口格才有 8 项详情，统一存 fields(JSON)，避免 8 列硬编码，
                    也方便将来加字段而不动表结构。
· display_field     为空字符串 ⇒ 跟随所属图的全局外显字段；非空 ⇒ 该格单独覆盖。
· row_span/col_span 合并单元格：只有锚点格建记录，被覆盖的坐标不建记录。
· 与「硬件台账 resource_items」完全解耦（需求明确不联动），本模块不引用任何台账表。
"""
from sqlalchemy import (
    Column, Integer, String, Text, JSON, DateTime, Float, Index,
)
from datetime import datetime
from models.database import Base


# 网口格的 8 项固定详情（顺序即前端表单顺序）
PORT_FIELDS = [
    ("status", "状态"),
    ("user", "使用人"),
    ("dept", "部门"),
    ("device", "设备"),
    ("mac", "MAC"),
    ("ip", "IP"),
    ("switch_port", "交换机口"),
    ("remark", "备注"),
]


class PortMap(Base):
    """工位网口图 —— 一张平面图"""
    __tablename__ = "portmaps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, comment="图纸名称，如 4F 办公区")
    floor = Column(String(32), default="", comment="楼层/区域标识，如 4F")
    rows = Column(Integer, default=22, comment="行数")
    cols = Column(Integer, default=24, comment="列数")
    cell_w = Column(Integer, default=76, comment="单格宽度 px")
    cell_h = Column(Integer, default=51, comment="单格高度 px")
    display_field = Column(String(32), default="", comment="全局外显字段 key，空=不显示")
    legend = Column(JSON, default=list, comment="图例 [{label,color}]")
    remark = Column(Text, default="", comment="备注")
    sort_order = Column(Integer, default=0, comment="排序号")
    creator = Column(String(128), default="admin", comment="创建人")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class PortCell(Base):
    """工位网口图 —— 一个格子"""
    __tablename__ = "portmap_cells"

    id = Column(Integer, primary_key=True, autoincrement=True)
    map_id = Column(Integer, nullable=False, index=True, comment="所属图纸 portmaps.id")
    row = Column(Integer, nullable=False, default=0, comment="行索引，0 起")
    col = Column(Integer, nullable=False, default=0, comment="列索引，0 起")
    row_span = Column(Integer, default=1, comment="纵向合并行数")
    col_span = Column(Integer, default=1, comment="横向合并列数")
    label = Column(String(64), default="", comment="格内主标签，网口格=网口编号，如 A11")
    kind = Column(String(16), default="port", comment="port 网口 / aisle 过道 / zone 功能区 / cabinet 柜体 / tag 编号")
    bg_color = Column(String(16), default="", comment="填充色 #RRGGBB，空=用 kind 默认色")
    display_field = Column(String(32), default="", comment="本格外显字段覆盖，空=跟随图纸")
    fields = Column(JSON, default=dict, comment="8 项详情 {status,user,dept,device,mac,ip,switch_port,remark}")
    sort_order = Column(Integer, default=0, comment="绘制顺序（同位置冲突时后写覆盖前写）")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


Index("ix_portmap_cells_map_row_col", PortCell.map_id, PortCell.row, PortCell.col)


class PortMapUndo(Base):
    """合并 / 拆分 / 还原 之前的整图快照，用于「撤回」（后进先出）。"""
    __tablename__ = "portmap_undo"

    id = Column(Integer, primary_key=True, autoincrement=True)
    map_id = Column(Integer, nullable=False, index=True, comment="所属图纸 portmaps.id")
    action = Column(String(32), default="", comment="动作名：合并/拆分/还原原图")
    payload = Column(JSON, default=list, comment="快照：该图全部格子（不含 id/map_id）")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
