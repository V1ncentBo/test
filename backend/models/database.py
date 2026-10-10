"""数据库模型定义 (含用户管理)"""
from sqlalchemy import Column, Integer, BigInteger, String, Float, Double, DateTime, Text, Boolean, create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from config import DATABASE_URL
import enum

engine = create_engine(DATABASE_URL, pool_size=20, max_overflow=40, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class DeviceType(str, enum.Enum):
    PHYSICAL = "physical"
    KVM = "kvm"
    VMWARE = "vmware"
    VIRTUALBOX = "virtualbox"
    SNMP = "snmp"


class AlertLevel(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class User(Base):
    """系统用户表"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, unique=True, index=True, comment="登录用户名")
    password_hash = Column(String(256), nullable=False, comment="bcrypt 密码哈希")
    display_name = Column(String(128), default="", comment="显示名称")
    role = Column(String(32), nullable=False, default="user", comment="角色: admin / user")
    email = Column(String(128), default="", comment="邮箱")
    phone = Column(String(32), default="", comment="手机号")
    is_enabled = Column(Boolean, default=True, comment="是否启用")
    permissions = Column(Text, default="", comment="权限 JSON 数组字符串（模块 key 列表，admin 角色恒为全部）")
    last_login_at = Column(DateTime, nullable=True, comment="最后登录时间")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class MachineInfo(Base):
    """设备信息表"""
    __tablename__ = "machine_info"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, comment="设备名称")
    ip = Column(String(45), nullable=False, index=True, comment="IP地址")
    port = Column(Integer, default=22, comment="连接端口")
    device_type = Column(String(32), default="physical", comment="设备类型")
    group_name = Column(String(64), default="default", index=True, comment="分组")
    tags = Column(String(256), default="", comment="标签逗号分隔")
    username = Column(String(64), default="root", comment="SSH用户名")
    password = Column(String(128), default="", comment="SSH密码")
    monitor_enabled = Column(Boolean, default=True, comment="监控开关")
    online_status = Column(String(16), default="unknown", comment="在线状态")
    # SNMP 设备参数（交换机/路由器）
    snmp_community = Column(String(64), default="public", comment="SNMP community 字符串")
    snmp_version = Column(String(8), default="v2c", comment="SNMP 版本 v1/v2c/v3")
    snmp_port = Column(Integer, default=161, comment="SNMP 端口")
    cpu_threshold = Column(Float, default=90.0, comment="CPU告警阈值")
    memory_threshold = Column(Float, default=90.0, comment="内存告警阈值")
    disk_threshold = Column(Float, default=85.0, comment="磁盘告警阈值")
    parent_id = Column(Integer, nullable=True, default=None, comment="父级物理机ID，NULL=物理机")
    remark = Column(Text, default="", comment="备注")
    # PVE 虚拟化纳管（宿主机 + 其子虚拟机）
    pve_token = Column(String(256), default="", comment="PVE API Token (格式 user@realm!tokenid=secret)")
    pve_port = Column(Integer, default=8006, comment="PVE API 端口")
    pve_node = Column(String(64), default="", comment="PVE 节点名(自动发现并缓存)")
    pve_vmid = Column(Integer, nullable=True, default=None, comment="PVE 虚拟机 ID (VM 子设备唯一标识)")
    # PVE 宿主机 SSH 凭据（用于绕过 API token 权限墙，直接反查 VM 真实 IP）
    pve_ssh_user = Column(String(64), default="root", comment="PVE 宿主机 SSH 用户名")
    pve_ssh_pass = Column(String(512), default="", comment="PVE 宿主机 SSH 密码(AES加密)")
    pve_ssh_key = Column(Text, default="", comment="PVE 宿主机 SSH 私钥(可选, PEM 文本)")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    top_processes = Column(Text, default="[]", comment="TOP5进程JSON")
    # 维护窗口（MAINT-20260917）：窗口内抑制该设备的指标/端口告警。
    # 列已存在于 machine_info（由 advanced_routes 的维护 API 写入），此处补 ORM 映射
    # 以便告警链路（alert_service._in_maintenance）能直接读到，无需额外原生 SQL。
    maintenance_mode = Column(Boolean, default=False, comment="维护模式开关")
    maintenance_until = Column(DateTime, nullable=True, comment="维护截止时间，NULL=手动常驻")
    # ── HWSTATIC-20261010：机器「固定属性」快照 ──────────────────────────────
    # 详情页下方卡片里有些字段对一台机器是**恒定的**（CPU 核数 / 内存总容量 / 磁盘总容量 /
    # 交换分区总容量 / 网卡协商速率 / 内核文件句柄上限），不随时间变化。
    # 但它们的可获取性依赖于有采集器在持续上报：拉模型（宿主机 API）拿不到绝对值，
    # 跨网段/离线机器也可能长期拿不到 ⇒ 详情页只能显示 `-`。
    # ⇒ 由 agent 在**首次识别**时上报一次并落库到这里，详情页在实时数据缺失时回填。
    #    需要刷新时，在目标机**重跑一次安装脚本**即可。
    # ⚠ 只在值 > 0 时覆盖：绝不能用一次采集失败产生的 0 覆盖掉已有的真实值。
    hw_cpu_cores = Column(Integer, default=0, comment="固定属性:CPU核数")
    # ⛔ 必须用 DOUBLE（不是 FLOAT）：MySQL FLOAT 是单精度，读回来的值与 agent 上报的
    #    双精度值**永远不相等** ⇒ 「值变了才刷时间戳」的判定会失效。
    hw_memory_total_gb = Column(Double, default=0.0, comment="固定属性:内存总容量GB")
    hw_disk_total_gb = Column(Double, default=0.0, comment="固定属性:磁盘总容量GB")
    hw_swap_total_gb = Column(Double, default=0.0, comment="固定属性:交换分区总容量GB")
    hw_net_link_speed = Column(Integer, default=0, comment="固定属性:网卡协商速率Mbps")
    hw_file_handles_max = Column(BigInteger, default=0, comment="固定属性:内核文件句柄上限")
    hw_collected_at = Column(DateTime, nullable=True, comment="固定属性识别(或刷新)时间")


class AlertLog(Base):
    """告警记录表"""
    __tablename__ = "alert_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    machine_id = Column(Integer, nullable=False, index=True, comment="设备ID")
    alert_type = Column(String(64), nullable=False, comment="告警类型")
    alert_level = Column(String(16), nullable=False, default="warning", comment="告警级别")
    metric_name = Column(String(64), comment="指标名称")
    current_value = Column(Float, comment="当前值")
    threshold_value = Column(Float, comment="阈值")
    message = Column(Text, nullable=False, comment="告警内容")
    ai_analysis = Column(Text, comment="AI分析结果")
    status = Column(String(16), default="unresolved", comment="处理状态")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="发生时间")
    resolved_at = Column(DateTime, comment="解决时间")





class AIAnalysisLog(Base):
    """AI分析记录表"""
    __tablename__ = "ai_analysis_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    machine_id = Column(Integer, index=True, comment="设备ID")
    analysis_type = Column(String(64), nullable=False, comment="分析类型")
    query_text = Column(Text, comment="用户提问/分析指令")
    diagnosis = Column(Text, comment="故障诊断结果")
    prediction = Column(Text, comment="趋势预测结果")
    optimization = Column(Text, comment="优化建议")
    raw_response = Column(Text, comment="原始AI回复")
    report_path = Column(String(256), comment="报告文件路径")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="分析时间")


class ReportRecord(Base):
    """运维报告存档表"""
    __tablename__ = "report_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period = Column(String(16), nullable=False, comment="日报/周报/月报")
    content = Column(Text, nullable=False, comment="报告内容 Markdown")
    machine_count = Column(Integer, default=0, comment="设备数")
    alert_count = Column(Integer, default=0, comment="告警数")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="生成时间")


class DbInstance(Base):
    """数据库实例监控表 — 只读采集，密码 Fernet 加密存储。"""
    __tablename__ = "db_instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, unique=True, index=True, comment="实例名称")
    type = Column(String(32), nullable=False, default="mysql", comment="类型: mysql/postgresql/mongodb/redis/elasticsearch/clickhouse")
    host = Column(String(128), nullable=False, comment="主机地址")
    port = Column(Integer, default=3306, comment="端口")
    account = Column(String(128), default="", comment="监控账号")
    password_enc = Column(Text, default="", comment="密码(Fernet加密)")
    template = Column(String(64), default="", comment="指标模板 key（如 default-rdb / es-log）")
    readonly = Column(Boolean, default=True, comment="是否强制只读")
    enabled = Column(Boolean, default=True, comment="是否纳入采集")
    tags = Column(String(256), default="", comment="标签逗号分隔")
    status = Column(String(16), default="unknown", comment="在线状态")
    last_metrics = Column(Text, default="{}", comment="最近一次指标JSON")
    last_error = Column(Text, default="", comment="最近一次错误信息")
    extra_params = Column(Text, default="{}", comment="类型相关扩展参数JSON")
    last_seen = Column(DateTime, nullable=True, comment="最近采集时间")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


def init_db():
    """初始化数据库表"""
    Base.metadata.create_all(bind=engine)
    ensure_pve_columns()
    ensure_user_columns()
    ensure_db_columns()
    ensure_hw_columns()


def ensure_user_columns():
    """兼容已部署环境：users 表可能已存在但缺少 permissions 列。
    通过 SHOW COLUMNS 检测并 ALTER TABLE 补齐。"""
    cols_to_add = [
        ("permissions", "TEXT NULL"),
    ]
    try:
        with engine.connect() as conn:
            existing = {row[0] for row in conn.execute(text("SHOW COLUMNS FROM users")).fetchall()}
            for col, ddl in cols_to_add:
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))
                    conn.commit()
                    print(f"[ensure] added column users.{col}")
    except Exception as e:
        print(f"[ensure] user columns error: {e}")


def ensure_pve_columns():
    """兼容已部署环境：machine_info 表可能已存在但缺少 PVE 扩展列。
    通过 SHOW COLUMNS 检测并 ALTER TABLE 补齐，规避 create_all 不自动加列的限制。"""
    cols_to_add = [
        ("pve_token", "VARCHAR(256) NOT NULL DEFAULT ''"),
        ("pve_port", "INT NOT NULL DEFAULT 8006"),
        ("pve_node", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("pve_vmid", "INT NULL"),
        ("pve_ssh_user", "VARCHAR(64) NOT NULL DEFAULT 'root'"),
        ("pve_ssh_pass", "VARCHAR(512) NOT NULL DEFAULT ''"),
        ("pve_ssh_key", "TEXT NOT NULL"),
    ]
    try:
        with engine.connect() as conn:
            existing = {row[0] for row in conn.execute(text("SHOW COLUMNS FROM machine_info")).fetchall()}
            for col, ddl in cols_to_add:
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE machine_info ADD COLUMN {col} {ddl}"))
                    conn.commit()
                    print(f"[ensure] added column machine_info.{col}")
    except Exception as e:
        print(f"[ensure] pve columns error: {e}")


def ensure_db_columns():
    """兼容已部署环境：db_instances 表可能已存在但缺少扩展列，ALTER 补齐。"""
    cols_to_add = [
        ("readonly", "TINYINT(1) NOT NULL DEFAULT 1"),
        ("enabled", "TINYINT(1) NOT NULL DEFAULT 1"),
        ("last_metrics", "TEXT NOT NULL"),
        ("last_error", "TEXT NOT NULL"),
        ("extra_params", "TEXT NOT NULL"),
        ("last_seen", "DATETIME NULL"),
        ("template", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ]
    try:
        with engine.connect() as conn:
            existing = {row[0] for row in conn.execute(text("SHOW COLUMNS FROM db_instances")).fetchall()}
            for col, ddl in cols_to_add:
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE db_instances ADD COLUMN {col} {ddl}"))
                    conn.commit()
                    print(f"[ensure] added column db_instances.{col}")
    except Exception as e:
        print(f"[ensure] db columns error: {e}")


def ensure_hw_columns():
    """HWSTATIC-20261010：machine_info 补「机器固定属性」快照列。

    与上面两个 ensure_* 同一套路：SHOW COLUMNS 检测 + ALTER TABLE 幂等补齐，
    规避 create_all 不会给已存在表加列的限制 ⇒ 后端重启即自动完成迁移，无需手工 SQL。
    """
    cols_to_add = [
        ("hw_cpu_cores", "INT NOT NULL DEFAULT 0"),
        ("hw_memory_total_gb", "DOUBLE NOT NULL DEFAULT 0"),
        ("hw_disk_total_gb", "DOUBLE NOT NULL DEFAULT 0"),
        ("hw_swap_total_gb", "DOUBLE NOT NULL DEFAULT 0"),
        ("hw_net_link_speed", "INT NOT NULL DEFAULT 0"),
        ("hw_file_handles_max", "BIGINT NOT NULL DEFAULT 0"),
        ("hw_collected_at", "DATETIME NULL"),
    ]
    # 早期版本把三个容量列建成了 FLOAT(单精度)——单精度读回值 ≠ agent 的双精度值，
    # 会让「值变了才刷时间戳」的判定恒为真。这里做一次幂等的类型纠正。
    cols_fix_double = ["hw_memory_total_gb", "hw_disk_total_gb", "hw_swap_total_gb"]
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SHOW COLUMNS FROM machine_info")).fetchall()
            existing = {row[0] for row in rows}
            types = {row[0]: str(row[1]).lower() for row in rows}
            for col, ddl in cols_to_add:
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE machine_info ADD COLUMN {col} {ddl}"))
                    conn.commit()
                    print(f"[ensure] added column machine_info.{col}")
            for col in cols_fix_double:
                if col in types and types[col] == "float":
                    conn.execute(text(f"ALTER TABLE machine_info MODIFY {col} DOUBLE NOT NULL DEFAULT 0"))
                    conn.commit()
                    print(f"[ensure] fixed column type machine_info.{col} FLOAT -> DOUBLE")
    except Exception as e:
        print(f"[ensure] hw columns error: {e}")


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
