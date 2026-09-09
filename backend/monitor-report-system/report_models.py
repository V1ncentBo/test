"""
报表系统数据模型
扩展 report_record 表 + 新增调度配置表 + 报表模板表
"""
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Float, JSON, Enum as SqlEnum, ForeignKey
)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

# === 枚举定义 ===

class ReportType(str, enum.Enum):
    DAILY = "daily"      # 日报
    WEEKLY = "weekly"    # 周报
    MONTHLY = "monthly"  # 月报

class ReportStatus(str, enum.Enum):
    PENDING = "pending"      # 待生成
    GENERATING = "generating"  # 生成中
    COMPLETED = "completed"    # 已完成
    FAILED = "failed"         # 失败

class DeliveryMethod(str, enum.Enum):
    WEB = "web"           # 平台内查看
    EMAIL = "email"       # 邮件发送
    DINGTALK = "dingtalk" # 钉钉推送
    ALL = "all"           # 全部渠道

# === 核心报表记录表 ===

class ReportRecord:
    """报表记录 - 每次生成的报表"""
    __tablename__ = "report_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_type = Column(SqlEnum(ReportType), nullable=False, comment="报表类型: daily/weekly/monthly")
    title = Column(String(256), nullable=False, comment="报表标题")
    period_start = Column(DateTime, nullable=False, comment="报表周期开始")
    period_end = Column(DateTime, nullable=False, comment="报表周期结束")

    # 内容
    html_content = Column(Text, comment="HTML 格式报表内容")
    json_summary = Column(JSON, comment="JSON 格式摘要数据（供前端图表渲染）")
    ai_analysis = Column(Text, comment="AI 分析结果")

    # 指标快照
    metrics_snapshot = Column(JSON, comment="关键指标快照")
    alert_summary = Column(JSON, comment="告警汇总统计")
    device_stats = Column(JSON, comment="设备统计数据")

    # 状态
    status = Column(SqlEnum(ReportStatus), default=ReportStatus.PENDING)
    error_message = Column(Text, comment="失败原因")
    generated_at = Column(DateTime, default=datetime.now, comment="生成时间")

    # 分发
    delivery_method = Column(SqlEnum(DeliveryMethod), default=DeliveryMethod.WEB)
    delivery_status = Column(String(64), default="pending", comment="分发状态")
    delivered_at = Column(DateTime, comment="分发时间")

    # 元数据
    created_by = Column(String(64), default="scheduler", comment="创建方式: scheduler/manual")
    file_path = Column(String(512), comment="导出文件路径")

    # 周报/月报特有字段
    trend_data = Column(JSON, comment="趋势数据（周报/月报）")
    health_scores = Column(JSON, comment="设备健康评分")
    capacity_planning = Column(JSON, comment="容量规划建议")
    anomaly_patterns = Column(JSON, comment="异常模式分析")


# === 调度配置表 ===

class ReportSchedule:
    """报表调度配置 - 控制定时任务参数"""
    __tablename__ = "report_schedule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_type = Column(SqlEnum(ReportType), nullable=False, unique=True)
    enabled = Column(Boolean, default=True, comment="是否启用")

    # Cron 表达式
    cron_expression = Column(String(32), comment="Cron 表达式")
    # 日报: 每天 8:00  → "0 8 * * *"
    # 周报: 每周一 9:00 → "0 9 * * 1"
    # 月报: 每月1日 10:00 → "0 10 1 * *"

    # 分发配置
    delivery_methods = Column(JSON, default=["web"], comment="分发渠道列表")
    email_recipients = Column(JSON, default=[], comment="邮件接收人列表")
    dingtalk_enabled = Column(Boolean, default=False, comment="钉钉推送开关")

    # AI 分析配置
    ai_enabled = Column(Boolean, default=True, comment="是否启用 AI 分析")
    ai_model = Column(String(64), default="deepseek-chat", comment="AI 模型")

    # 数据保留
    retention_days = Column(Integer, default=365, comment="报表保留天数")
    auto_archive = Column(Boolean, default=True, comment="自动归档旧报表")

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# === 报表模板表 ===

class ReportTemplate:
    """报表 HTML 模板 - 不同报表类型使用不同的模板"""
    __tablename__ = "report_template"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_type = Column(SqlEnum(ReportType), nullable=False, unique=True)
    name = Column(String(128), comment="模板名称")
    description = Column(Text, comment="模板描述")
    html_template = Column(Text, nullable=False, comment="HTML 模板内容")
    css_style = Column(Text, comment="CSS 样式")
    chart_configs = Column(JSON, comment="图表配置（ECharts option）")

    # 版本
    version = Column(String(16), default="1.0")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
