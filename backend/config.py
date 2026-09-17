"""全局配置"""
import os
from urllib.parse import quote_plus
from datetime import datetime
from zoneinfo import ZoneInfo

# 北京时间（UTC+8）— 全系统唯一时间基准
BJT = ZoneInfo("Asia/Shanghai")

def bj_now():
    """返回带时区的北京时间"""
    return datetime.now(BJT)

# MySQL 配置
MYSQL_HOST = os.getenv("MYSQL_HOST", "mysql")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "monitor")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "monitor_platform")
DATABASE_URL = f"mysql+pymysql://{MYSQL_USER}:{quote_plus(MYSQL_PASSWORD)}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"

# InfluxDB 配置
INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://influxdb:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "monitor-org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "machine_metrics")

# AI 大模型配置
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "qwen-turbo")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

# 平台对外访问地址（用于邮件/钉钉告警链接、Agent 安装脚本等）
# 迁移到新机器时只需在 .env 修改此值，无需改代码
PLATFORM_PUBLIC_URL = os.getenv("PLATFORM_PUBLIC_URL", "").rstrip("/")

# 采集配置
COLLECT_INTERVAL = int(os.getenv("COLLECT_INTERVAL", "10"))

# 告警默认阈值
DEFAULT_ALERT_THRESHOLDS = {
    "cpu_percent": 90,
    "memory_percent": 90,
    "disk_percent": 85,
    "network_in_mbps": 900,
    "network_out_mbps": 900,
    "load_1m": 10,
}
