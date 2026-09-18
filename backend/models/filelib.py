"""运维文件库 —— 镜像 / 服务包 元数据模型

设计要点：
- 文件本体落宿主机 /opt/monitor-platform/filelib/（bind 进容器 /app/filelib），
  不进容器可写层、不进 MySQL blob，避免撑爆镜像与数据库。
- 落盘名 stored_name 由 sha256 前缀 + 时间戳 + 白名单字符组成，
  原始名 name 只用于展示与下载回填，绝不参与路径拼接（防路径穿越）。
"""
from sqlalchemy import Column, Integer, String, BigInteger, DateTime, Text, Index
from datetime import datetime

from models.database import Base


class FileAsset(Base):
    """文件资产元数据"""
    __tablename__ = "file_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True, comment="原始文件名（含扩展名）")
    stored_name = Column(String(255), nullable=False, unique=True, comment="落盘文件名（sha16_ts_安全名）")
    category = Column(String(32), nullable=False, default="doc-other", index=True,
                      comment="分类: os-image/app-package/driver-firmware/script-tool/doc-other")
    distro = Column(String(64), default="", comment="发行版/厂商，如 Ubuntu / Nginx")
    version = Column(String(64), default="", comment="版本号，如 22.04.4")
    arch = Column(String(24), default="any", comment="架构: x86_64/aarch64/any")
    size_bytes = Column(BigInteger, nullable=False, default=0, comment="字节数")
    sha256 = Column(String(64), nullable=False, default="", index=True, comment="SHA256 校验值")
    note = Column(String(500), default="", comment="备注/用途说明")
    tags = Column(String(255), default="", comment="逗号分隔标签")
    uploader = Column(String(64), default="", index=True, comment="上传人")
    download_count = Column(Integer, nullable=False, default=0, comment="下载次数")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="上传时间")
    deleted_at = Column(DateTime, nullable=True, comment="软删除时间（预留）")

    __table_args__ = (
        Index("ix_file_assets_cat_created", "category", "created_at"),
    )


class FileDownloadLog(Base):
    """下载审计日志"""
    __tablename__ = "file_download_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, nullable=False, index=True, comment="file_assets.id")
    username = Column(String(64), default="", index=True, comment="下载人")
    client_ip = Column(String(45), default="", comment="来源 IP（兼容 IPv6）")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="下载时间")
