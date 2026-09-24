"""node_exporter Prometheus 指标抓取器

单位约定（与前端展示口径一致，勿随意改动）：
  disk_read_mbps / disk_write_mbps   → MB/s（字段名为历史遗留，前端 label 实为 " MB/s"）
  network_in_mbps / network_out_mbps → Mbps
  net_link_speed                     → Mbps
  swap_in_rate / swap_out_rate       → 页/s
  mem_page_faults / context_switches → 次/s
  iowait / cpu_steal / cpu_percent   → %

速率类字段由「相邻两次抓取的计数器增量 ÷ 实际间隔」换算：
  - 首次抓取无历史 → 返回 0（下个周期（默认 15s）即出真实值）
  - 计数器回退（node_exporter 重启）→ 该周期记 0，不产生负值尖刺
"""
import re
import time
import urllib.request
from typing import Optional

# ── 速率类字段需要跨调用保存上一轮计数器：{ "ip:port": (ts, counters) } ──
_PREV = {}
# ── 抓取失败计数：用于日志节流（连续失败只在首次与每 20 次时打印）──
_FAILS = {}
_STATE_LIMIT = 5000

# 分层/虚拟块设备：与底层物理盘同时上报，一起求和会重复计数（如 LVM dm-* 叠在 nvme 上）
_VIRTUAL_DISK = re.compile(r"^(dm-|md\d|loop|ram|zram|sr|fd\d|nbd)")

# 虚拟/别名网卡：其上流量与其物理从属网卡重叠，求和会重复计数；仅当无物理网卡时才退回使用
_VIRTUAL_IFACE = re.compile(
    r"^(lo$|docker|br-|veth|virbr|vnet|tap\d|tun\d|bond|dummy|kube|flannel|cali|cni|wg\d|zt)"
)

# 伪文件系统：容量不代表真实磁盘，不能作为整机容量口径
_PSEUDO_FS = {
    "tmpfs", "devtmpfs", "devpts", "overlay", "squashfs", "ramfs", "proc", "sysfs",
    "cgroup", "cgroup2", "autofs", "mqueue", "debugfs", "tracefs", "securityfs",
    "pstore", "bpf", "configfs", "fusectl", "hugetlbfs", "binfmt_misc", "efivarfs",
    "nsfs", "rpc_pipefs", "nfsd", "selinuxfs", "fuse.gvfsd-fuse", "none",
}

_LABEL_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')


def scrape_node(ip: str, port: int = 9100, timeout: int = 5) -> Optional[dict]:
    """抓取 node_exporter /metrics 并解析为监控字段，失败返回 None"""
    key = f"{ip}:{port}"
    try:
        url = f"http://{ip}:{port}/metrics"
        resp = urllib.request.urlopen(url, timeout=timeout)
        raw = resp.read().decode("utf-8")
    except Exception as e:
        n = _FAILS.get(key, 0) + 1
        _FAILS[key] = n
        if n == 1 or n % 20 == 0:
            print(f"[NodeScraper] {key} fetch failed (连续 {n} 次): {e}")
        return None

    metrics = _parse_prometheus(raw)
    if not metrics:
        return None
    if _FAILS.pop(key, None):
        print(f"[NodeScraper] {key} recovered")

    idx = _index(metrics)
    cur = _counters(idx)
    now = time.time()
    prev_state = _PREV.get(key)
    prev = prev_state[1] if prev_state else None
    dt = (now - prev_state[0]) if prev_state else 0.0
    if len(_PREV) >= _STATE_LIMIT:
        _PREV.clear()
    _PREV[key] = (now, cur)

    return _build_fields(metrics, idx, cur, prev, dt)


def _parse_prometheus(raw: str) -> dict:
    """解析 Prometheus 文本格式为 {metric_name: value}，带标签的保留 name{labels}"""
    result = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        metric_line = parts[0]
        try:
            val = float(parts[1])
        except ValueError:
            continue
        result[metric_line] = val
    return result


def _labels(key: str) -> dict:
    """从 'name{k="v",...}' 中取出标签字典"""
    brace = key.find("{")
    if brace < 0:
        return {}
    return dict(_LABEL_RE.findall(key[brace + 1:].rstrip("}")))


def _index(metrics: dict) -> dict:
    """一次性建索引 {metric_name: [(labels, value), ...]}，避免反复遍历全部指标"""
    idx = {}
    for key, val in metrics.items():
        brace = key.find("{")
        if brace < 0:
            idx.setdefault(key, []).append(({}, val))
        else:
            idx.setdefault(key[:brace], []).append((_labels(key), val))
    return idx


# ────────────────────────── 兼容旧调用方的工具函数 ──────────────────────────

def _get_label_values(m: dict, name: str, label: str) -> list:
    """获取带特定标签的指标值列表"""
    vals = []
    for key, val in m.items():
        if key.startswith(name + "{"):
            if label in key:
                vals.append(val)
    return vals


def _get_first_without(m: dict, name: str, exclude: str) -> float:
    """获取第一个不包含 exclude 的指标值"""
    for key, val in m.items():
        if key.startswith(name + "{") and exclude not in key:
            return val
    return 0


# ────────────────────────────── 维度选择 ──────────────────────────────

def _disk_devices(idx: dict) -> set:
    """选出代表真实物理 IO 的块设备名（排除 dm-/loop/md 等分层设备）"""
    devs = set()
    for name in ("node_disk_read_bytes_total", "node_disk_written_bytes_total",
                 "node_disk_reads_completed_total"):
        for lb, _v in idx.get(name, ()):
            dev = lb.get("device", "")
            if dev:
                devs.add(dev)
    physical = {d for d in devs if not _VIRTUAL_DISK.match(d)}
    return physical or devs


def _net_devices(idx: dict) -> set:
    """选出代表真实流量的网卡名（排除 lo 与 docker/br-/veth 等虚拟网卡）"""
    devs = set()
    for name in ("node_network_receive_bytes_total", "node_network_transmit_bytes_total"):
        for lb, _v in idx.get(name, ()):
            dev = lb.get("device", "")
            if dev:
                devs.add(dev)
    physical = {d for d in devs if not _VIRTUAL_IFACE.match(d)}
    return physical or {d for d in devs if d != "lo"}


def _net_devices_all(idx: dict) -> set:
    """全部非 lo 网卡（用于 up / speed 判定，不参与流量求和）"""
    devs = set()
    for name in ("node_network_up", "node_network_speed_bytes",
                 "node_network_receive_bytes_total"):
        for lb, _v in idx.get(name, ()):
            dev = lb.get("device", "")
            if dev and dev != "lo":
                devs.add(dev)
    return devs


def _sum_device(idx: dict, name: str, devices: set) -> float:
    """累加指定网卡/磁盘上的指标"""
    return sum(v for lb, v in idx.get(name, ()) if lb.get("device") in devices)


def _fs_pick(idx: dict) -> Optional[dict]:
    """选代表整机容量的文件系统：优先 mountpoint="/"（容器内取不到则退回真实 fs 中最大者）"""
    keys = {}
    for name in ("node_filesystem_size_bytes", "node_filesystem_avail_bytes",
                 "node_filesystem_files", "node_filesystem_files_free"):
        for lb, v in idx.get(name, ()):
            # 标签组合 (device, fstype, mountpoint) 在 node_filesystem_* 之间稳定一致
            keys.setdefault((lb.get("device", ""), lb.get("fstype", ""),
                             lb.get("mountpoint", "")), {})[name] = v
    if not keys:
        return None

    picked = None
    for combo in keys:
        if combo[2] == "/":
            picked = combo
            break
    if picked is None:
        real = [c for c in keys
                if c[1] not in _PSEUDO_FS and keys[c].get("node_filesystem_size_bytes", 0) > 0]
        if real:
            picked = max(real, key=lambda c: keys[c].get("node_filesystem_size_bytes", 0))
    if picked is None:
        return None

    f = keys[picked]
    return {
        "mountpoint": picked[2],
        "size": f.get("node_filesystem_size_bytes", 0.0),
        "avail": f.get("node_filesystem_avail_bytes", 0.0),
        "files": f.get("node_filesystem_files", 0.0),
        "files_free": f.get("node_filesystem_files_free", 0.0),
    }


# ────────────────────────────── 计数器快照 ──────────────────────────────

def _counters(idx: dict) -> dict:
    """抽取所有单调递增计数器（含 CPU 模式累计、磁盘/网卡字节与次数）及其它瞬时量"""
    c = {}
    cpu_total = cpu_idle = cpu_iowait = cpu_steal = 0.0
    cores = set()
    for lb, v in idx.get("node_cpu_seconds_total", ()):
        cpu_total += v
        mode = lb.get("mode", "")
        if mode == "idle":
            cpu_idle += v
            if lb.get("cpu") is not None:
                cores.add(lb["cpu"])
        elif mode == "iowait":
            cpu_iowait += v
        elif mode == "steal":
            cpu_steal += v
    c["cpu_total"] = cpu_total
    c["cpu_idle"] = cpu_idle
    c["cpu_iowait"] = cpu_iowait
    c["cpu_steal"] = cpu_steal
    c["cpu_cores"] = len(cores) or 1

    disk = _disk_devices(idx)
    c["disk_read_bytes"] = _sum_device(idx, "node_disk_read_bytes_total", disk)
    c["disk_write_bytes"] = _sum_device(idx, "node_disk_written_bytes_total", disk)
    c["disk_reads"] = _sum_device(idx, "node_disk_reads_completed_total", disk)
    c["disk_writes"] = _sum_device(idx, "node_disk_writes_completed_total", disk)

    net = _net_devices(idx)
    c["net_rx"] = _sum_device(idx, "node_network_receive_bytes_total", net)
    c["net_tx"] = _sum_device(idx, "node_network_transmit_bytes_total", net)

    c["pgfault"] = idx.get("node_vmstat_pgfault", [(None, 0.0)])[0][1]
    c["pswpin"] = idx.get("node_vmstat_pswpin", [(None, 0.0)])[0][1]
    c["pswpout"] = idx.get("node_vmstat_pswpout", [(None, 0.0)])[0][1]
    c["ctxt"] = idx.get("node_context_switches_total", [(None, 0.0)])[0][1]
    return c


def _rate(cur: dict, prev: Optional[dict], dt: float, key: str) -> float:
    """计数器增量 ÷ 实际间隔；无历史/计数器回退时返回 0"""
    if prev is None or dt <= 0:
        return 0.0
    delta = cur.get(key, 0.0) - prev.get(key, 0.0)
    return delta / dt if delta > 0 else 0.0


# ────────────────────────────── 字段组装 ──────────────────────────────

def _build_fields(m: dict, idx: Optional[dict] = None,
                  c: Optional[dict] = None, prev: Optional[dict] = None,
                  dt: float = 0.0) -> dict:
    """将 Prometheus 指标映射为平台监控字段"""
    if idx is None:
        idx = _index(m)
    if c is None:
        c = _counters(idx)

    def rate(key: str) -> float:
        return _rate(c, prev, dt, key)

    fields = {}

    # ---------- CPU ----------
    cpu_cores = int(c.get("cpu_cores") or 1)
    fields["cpu_cores"] = cpu_cores

    freqs = [v for _lb, v in idx.get("node_cpu_frequency_hertz", ()) if v > 0]
    fields["cpu_freq_mhz"] = int(round(sum(freqs) / len(freqs) / 1e6)) if freqs else 0

    # 使用率必须用「区间增量」而非开机至今累计值，否则被长期均值抹平
    d_total = c.get("cpu_total", 0.0) - (prev or {}).get("cpu_total", 0.0) if prev else 0.0
    if d_total > 0:
        d_idle = c["cpu_idle"] - prev["cpu_idle"]
        d_iowait = c["cpu_iowait"] - prev["cpu_iowait"]
        d_steal = c["cpu_steal"] - prev["cpu_steal"]
        fields["cpu_percent"] = round(max(0.0, min((1 - d_idle / d_total) * 100, 100.0)), 1)
        fields["iowait"] = round(max(0.0, min(d_iowait / d_total * 100, 100.0)), 1)
        fields["cpu_steal"] = round(max(0.0, min(d_steal / d_total * 100, 100.0)), 1)
    else:
        load1 = m.get("node_load1", 0)
        fields["cpu_percent"] = round(min(load1 / max(cpu_cores, 1) * 100, 100), 1)
        fields["iowait"] = 0.0
        fields["cpu_steal"] = 0.0

    load1 = m.get("node_load1", 0)
    fields["load_1m"] = round(load1, 2)
    fields["load_5m"] = round(m.get("node_load5", 0), 2)
    fields["load_15m"] = round(m.get("node_load15", 0), 2)

    # ---------- Memory ----------
    total = m.get("node_memory_MemTotal_bytes", 0)
    avail = m.get("node_memory_MemAvailable_bytes", 0)
    free = m.get("node_memory_MemFree_bytes", 0)
    buffers = m.get("node_memory_Buffers_bytes", 0)
    cached = m.get("node_memory_Cached_bytes", 0)
    swap_total = m.get("node_memory_SwapTotal_bytes", 0)
    swap_free = m.get("node_memory_SwapFree_bytes", 0)
    used = total - avail if avail > 0 else total - free - buffers - cached
    fields["memory_percent"] = round(used / total * 100, 1) if total > 0 else 0
    fields["memory_total"] = round(total / 1e9, 2)   # main.py 不转换，collector 别名写入 memory_total_gb
    fields["memory_used"] = round(used / 1e9, 2)
    fields["swap_used_gb"] = round((swap_total - swap_free) / 1e9, 1)
    fields["swap_in_rate"] = int(round(rate("pswpin")))    # 页/s
    fields["swap_out_rate"] = int(round(rate("pswpout")))  # 页/s
    fields["mem_page_faults"] = int(round(rate("pgfault")))  # 次/s

    # ---------- Disk 容量 ----------
    fs = _fs_pick(idx)
    if fs and fs["size"] > 0:
        total_gb = fs["size"] / 1e9
        avail_gb = fs["avail"] / 1e9
        used_gb = max(total_gb - avail_gb, 0.0)
        fields["disk_total_gb"] = round(total_gb, 1)
        fields["disk_used_gb"] = round(used_gb, 1)
        fields["disk_percent"] = round(used_gb / total_gb * 100, 1)
        if fs["files"] > 0:
            fields["inode_percent"] = round(
                max(fs["files"] - fs["files_free"], 0.0) / fs["files"] * 100, 1)
        else:
            fields["inode_percent"] = 0
    else:
        fields["disk_total_gb"] = 0
        fields["disk_used_gb"] = 0
        fields["disk_percent"] = 0
        fields["inode_percent"] = 0

    # ---------- Disk IO（字节→MB/s；次数→IOPS）----------
    fields["disk_read_mbps"] = round(rate("disk_read_bytes") / 1e6, 3)
    fields["disk_write_mbps"] = round(rate("disk_write_bytes") / 1e6, 3)
    fields["disk_read_iops"] = int(round(rate("disk_reads")))
    fields["disk_write_iops"] = int(round(rate("disk_writes")))

    # ---------- Network ----------
    # 字节/s → Mbps
    fields["network_in_mbps"] = round(rate("net_rx") * 8 / 1e6, 3)
    fields["network_out_mbps"] = round(rate("net_tx") * 8 / 1e6, 3)

    net = _net_devices(idx)
    fields["tcp_established"] = int(m.get("node_netstat_Tcp_CurrEstab", 0) or 0)
    fields["network_connections"] = fields["tcp_established"]

    # 错误/丢包为开机至今累计值：alert_service 已将这些字段按增量口径判定，直接写累计
    fields["net_errors_total"] = int(_sum_device(idx, "node_network_receive_errs_total", net)
                                     + _sum_device(idx, "node_network_transmit_errs_total", net))
    fields["net_drops_total"] = int(_sum_device(idx, "node_network_receive_drop_total", net)
                                    + _sum_device(idx, "node_network_transmit_drop_total", net))

    # node_network_speed_bytes 实为 bytes/s（= Mbit/s × 1e6 / 8）；未连接网卡返回 -1（换算出 -125000）
    # 前端 label 为 " Mbps"，故取正向最大值 × 8 / 1e6
    all_ifaces = _net_devices_all(idx)
    best_speed = 0.0
    for lb, v in idx.get("node_network_speed_bytes", ()):
        if lb.get("device") in all_ifaces and v > 0:
            best_speed = max(best_speed, v)
    fields["net_link_speed"] = int(round(best_speed * 8 / 1e6))

    link_up = 0
    for lb, v in idx.get("node_network_up", ()):
        if lb.get("device") in all_ifaces and v >= 1:
            link_up = 1
            break
    fields["net_link_up"] = link_up

    # 监听端口数：node_exporter 未导出 listen 队列
    fields["listening_ports"] = int(_label_value(idx, "node_netstat_Tcp_Listen", None, None) or 0)

    # ---------- TCP ----------
    # TIME_WAIT：netstat 采集器不导出 Tcp_TimeWait，用 sockstat 的 tw（TIME-WAIT 套接字）替代
    fields["tcp_timewait"] = int(m.get("node_sockstat_TCP_tw", 0) or 0)
    # CLOSE_WAIT：node_exporter 无对应指标，保持 0
    fields["tcp_closewait"] = int(_label_value(idx, "node_netstat_Tcp_CloseWait", None, None) or 0)

    # ---------- System ----------
    # 进程总数 / 僵尸数：需 node_exporter 启用 processes 采集器（--collector.processes）
    pids = _label_value(idx, "node_processes_pids", None, None) or 0
    fields["process_count"] = int(pids)
    fields["zombie_count"] = int(_label_value(idx, "node_processes_state", "state", "Z") or 0)
    # D 态（不可中断睡眠）：/proc/stat 的 procs_blocked 即该口径；启用 processes 采集器时更精确
    d_state = _label_value(idx, "node_processes_state", "state", "D")
    fields["d_state_procs"] = int(
        d_state if d_state is not None else (m.get("node_procs_blocked", 0) or 0))

    fields["context_switches"] = int(round(rate("ctxt")))     # 次/s
    fields["oom_events"] = int(m.get("node_vmstat_oom_kill", 0) or 0)  # 累计
    # kernel_errors：node_exporter 无对应指标，保持 0
    fields["kernel_errors"] = 0
    fields["ntp_offset_ms"] = round((m.get("node_timex_offset_seconds", 0) or 0) * 1000, 3)
    fields["cpu_temp"] = _cpu_temp(idx)

    # ---------- 运行时长 / 句柄 / 登录用户 ----------
    t_now = m.get("node_time_seconds", 0)
    t_boot = m.get("node_boot_time_seconds", 0)
    fields["uptime_seconds"] = int(t_now - t_boot) if t_now > t_boot > 0 else 0
    fields["file_handles_used"] = int(m.get("node_filefd_allocated", 0) or 0)
    fields["file_handles_max"] = int(m.get("node_filefd_maximum", 0) or 0)
    fields["logged_users"] = int(_label_value(idx, "node_logged_in_users", None, None) or 0)

    # ---------- Disk health ----------
    # SMART / RAID 依赖 smartctl textfile 采集器，node_exporter 默认不提供
    fields["smart_health"] = "N/A"
    fields["raid_status"] = "N/A"
    fields["smart_reallocated"] = 0
    fields["smart_temp"] = 0
    fields["smart_lifetime"] = 0

    # ---------- Service status ----------
    units = _systemd_units(idx)
    if units is None:
        # 未启用 systemd 采集器：沿用旧行为（sshd 视为运行中），避免把「测不到」渲染成「服务宕机」
        fields["svc_sshd"] = 1
        fields["svc_cron"] = 0
        fields["svc_docker"] = 0
    else:
        fields["svc_sshd"] = int(units.get("sshd", 0))
        fields["svc_cron"] = int(units.get("crond", units.get("cron", 0)))
        fields["svc_docker"] = int(units.get("docker", units.get("containerd", 0)))

    fields["top_processes"] = "[]"

    return fields


def _label_value(idx: dict, name: str, label: Optional[str], value: Optional[str]):
    """按标签取值；label 为 None 时取该指标第一个值（兼容裸指标名）"""
    for lb, v in idx.get(name, ()):
        if label is None or lb.get(label) == value:
            return v
    return None


def _systemd_units(idx: dict) -> Optional[dict]:
    """{unit: 1/0}；node_exporter 未启用 systemd 采集器时返回 None"""
    metrics = idx.get("node_systemd_unit_state")
    if not metrics:
        return None
    units = {}
    for lb, v in metrics:
        name = lb.get("name", "")
        if not name:
            continue
        unit = name.split(".")[0]
        if lb.get("state") == "active":
            units[unit] = max(units.get(unit, 0.0), v)
        else:
            units.setdefault(unit, 0.0)
    return units


def _cpu_temp(idx: dict) -> float:
    """CPU 温度：优先 hwmon 中 Tctl/Tdie（AMD/Intel 核心温度），否则取全部探针最高值"""
    sensor_labels = {}
    for lb, v in idx.get("node_hwmon_sensor_label", ()):
        sensor_labels[(lb.get("chip"), lb.get("sensor"))] = lb.get("label", "")
    core = []
    allv = []
    for lb, v in idx.get("node_hwmon_temp_celsius", ()):
        if v <= 0 or v > 150:   # 过滤未接探针的占位/异常读数
            continue
        allv.append(v)
        if sensor_labels.get((lb.get("chip"), lb.get("sensor"))) in ("Tctl", "Tdie"):
            core.append(v)
    pick = core or allv
    return round(max(pick), 1) if pick else 0


def _count_cpu_cores(m: dict) -> int:
    """CPU 核数 = node_cpu_seconds_total 中不同 cpu 标签的个数（原实现按 idle 条数统计会误判）"""
    return int(_counters(_index(m)).get("cpu_cores") or 1)
