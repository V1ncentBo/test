"""Pydantic 请求/响应模型"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ========== 设备管理 ==========
class MachineCreate(BaseModel):
    name: str = Field(..., max_length=128)
    ip: str = Field(..., max_length=45)
    port: int = Field(default=22)
    device_type: str = Field(default="physical")
    group_name: str = Field(default="default")
    tags: str = Field(default="")
    username: str = Field(default="root")
    password: str = Field(default="")
    monitor_enabled: bool = Field(default=True)
    snmp_community: str = Field(default="public")
    snmp_version: str = Field(default="v2c")
    snmp_port: int = Field(default=161)
    cpu_threshold: float = Field(default=90.0)
    memory_threshold: float = Field(default=90.0)
    disk_threshold: float = Field(default=85.0)
    parent_id: Optional[int] = Field(default=None)
    remark: str = Field(default="")
    # PVE 虚拟化纳管
    pve_token: str = Field(default="")
    pve_port: int = Field(default=8006)
    pve_node: str = Field(default="")
    pve_vmid: Optional[int] = Field(default=None)
    # PVE 宿主机 SSH 凭据（反查 VM IP 用）
    pve_ssh_user: str = Field(default="root")
    pve_ssh_pass: str = Field(default="")
    pve_ssh_key: str = Field(default="")


class MachineUpdate(BaseModel):
    name: Optional[str] = None
    port: Optional[int] = None
    device_type: Optional[str] = None
    group_name: Optional[str] = None
    tags: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    monitor_enabled: Optional[bool] = None
    snmp_community: Optional[str] = None
    snmp_version: Optional[str] = None
    snmp_port: Optional[int] = None
    cpu_threshold: Optional[float] = None
    memory_threshold: Optional[float] = None
    disk_threshold: Optional[float] = None
    parent_id: Optional[int] = None
    remark: Optional[str] = None
    pve_token: Optional[str] = None
    pve_port: Optional[int] = None
    pve_node: Optional[str] = None
    pve_vmid: Optional[int] = None
    pve_ssh_user: Optional[str] = None
    pve_ssh_pass: Optional[str] = None
    pve_ssh_key: Optional[str] = None


class MachineResponse(BaseModel):
    id: int
    name: str
    ip: str
    port: int
    device_type: str
    group_name: str
    tags: str
    monitor_enabled: bool
    online_status: str
    snmp_community: str
    snmp_version: str
    snmp_port: int
    cpu_threshold: float
    memory_threshold: float
    disk_threshold: float
    parent_id: Optional[int] = None
    remark: str
    pve_token: str = ""
    pve_port: int = 8006
    pve_node: str = ""
    pve_vmid: Optional[int] = None
    pve_ssh_user: str = "root"
    pve_ssh_pass: str = ""
    pve_ssh_key: str = ""
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ========== 监控数据 ==========
class MetricData(BaseModel):
    class Config:
        extra = "ignore"
    machine_id: int
    timestamp: Optional[datetime] = None
    cpu_percent: float = 0.0
    cpu_cores: int = 0
    cpu_temp: float = 0.0
    memory_total: float = 0.0
    memory_used: float = 0.0
    memory_percent: float = 0.0
    swap_total: float = 0.0
    swap_used: float = 0.0
    disk_total: float = 0.0
    disk_used: float = 0.0
    disk_percent: float = 0.0
    disk_read_mbps: float = 0.0
    disk_write_mbps: float = 0.0
    network_in_mbps: float = 0.0
    network_out_mbps: float = 0.0
    network_connections: int = 0
    load_1m: float = 0.0
    load_5m: float = 0.0
    load_15m: float = 0.0
    uptime_seconds: int = 0
    process_count: int = 0
    # 扩展指标 ⭐⭐⭐
    zombie_count: int = 0
    logged_users: int = 0
    inode_percent: float = 0.0
    file_handles_used: int = 0
    file_handles_max: int = 0
    context_switches: int = 0
    # 扩展指标 ⭐⭐
    smart_health: str = "N/A"
    listening_ports: int = 0
    ntp_offset_ms: float = 0.0
    raid_status: str = "N/A"
    top_processes: str = "[]"
    # 第一批新增指标
    iowait: float = 0.0
    net_errors_total: int = 0
    net_drops_total: int = 0
    oom_events: int = 0
    tcp_established: int = 0
    tcp_timewait: int = 0
    tcp_closewait: int = 0
    smart_reallocated: int = 0
    smart_temp: int = 0
    smart_lifetime: int = 0
    # 第二批新增指标
    swap_in_rate: int = 0
    swap_out_rate: int = 0
    kernel_errors: int = 0
    net_link_speed: int = 0
    net_link_up: int = 0
    d_state_procs: int = 0
    svc_sshd: int = 1
    svc_cron: int = 1
    svc_docker: int = 1
    # 第三批新增指标
    cpu_freq_mhz: int = 0
    mem_page_faults: int = 0
    cpu_steal: float = 0.0
    disk_read_iops: int = 0
    disk_write_iops: int = 0


class MetricQuery(BaseModel):
    machine_id: int
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    metrics: Optional[List[str]] = None


class MetricPoint(BaseModel):
    time: str
    value: float


class MetricSeries(BaseModel):
    name: str
    unit: str
    data: List[MetricPoint]


# ========== 告警 ==========
class AlertThreshold(BaseModel):
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    disk_percent: Optional[float] = None


class AlertResponse(BaseModel):
    id: int
    machine_id: int
    alert_type: str
    alert_level: str
    metric_name: str
    current_value: float
    threshold_value: float
    message: str
    ai_analysis: Optional[str]
    status: str
    created_at: datetime
    resolved_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ========== AI 分析 ==========
class AIAnalysisRequest(BaseModel):
    machine_ids: List[int] = Field(..., min_length=1)
    analysis_type: str = Field(default="full")  # full/diagnosis/prediction/optimization
    query: Optional[str] = None
    time_range_hours: int = Field(default=24, ge=1, le=720)


class AIAnalysisResponse(BaseModel):
    id: int
    machine_id: int
    analysis_type: str
    query_text: Optional[str]
    overview: Optional[str] = None
    diagnosis: Optional[str] = None
    prediction: Optional[str] = None
    optimization: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ========== 统计概览 ==========
class DashboardStats(BaseModel):
    total_machines: int = 0
    physical_count: int = 0
    vm_count: int = 0
    online_count: int = 0
    offline_count: int = 0
    today_alerts: int = 0
    alert_machines: int = 0
    avg_cpu: float = 0.0
    avg_memory: float = 0.0
    avg_disk: float = 0.0


class BatchOperation(BaseModel):
    machine_ids: List[int]
    action: str  # enable/disable/delete/change_group
    value: Optional[str] = None
