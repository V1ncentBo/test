"""node_exporter Prometheus 指标抓取器"""
import re
import urllib.request
from typing import Optional


def scrape_node(ip: str, port: int = 9100, timeout: int = 5) -> Optional[dict]:
    """抓取 node_exporter /metrics 并解析为监控字段，失败返回 None"""
    try:
        url = f"http://{ip}:{port}/metrics"
        resp = urllib.request.urlopen(url, timeout=timeout)
        raw = resp.read().decode("utf-8")
    except Exception as e:
        print(f"[NodeScraper] {ip}:{port} fetch failed: {e}")
        return None

    metrics = _parse_prometheus(raw)
    if not metrics:
        return None

    return _build_fields(metrics)


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


def _build_fields(m: dict) -> dict:
    """将 Prometheus 指标映射为平台监控字段"""
    # CPU
    cpu_time = m.get("node_cpu_seconds_total", 0)
    cpu_idle = 0
    for key, val in m.items():
        if key == "node_cpu_seconds_total" and 'idle' in str(key):
            cpu_idle = val
    fields = {}
    fields["cpu_cores"] = _count_cpu_cores(m)
    fields["cpu_freq_mhz"] = round(m.get("node_cpu_frequency_hertz", 0) / 1e6, 1) if m.get("node_cpu_frequency_hertz", 0) > 0 else 0

    # CPU percent: sum of idle modes over all CPUs / total
    cpu_total = 0
    cpu_idle = 0
    for key, val in m.items():
        if key.startswith("node_cpu_seconds_total{"):
            cpu_total += val
            if 'idle' in key:
                cpu_idle += val
    load1 = m.get("node_load1", 0)
    if cpu_total > 0:
        fields["cpu_percent"] = round((1 - cpu_idle / cpu_total) * 100, 1)
    else:
        fields["cpu_percent"] = round(min(load1 / max(fields["cpu_cores"], 1) * 100, 100), 1)
    fields["load_1m"] = round(load1, 2)
    fields["load_5m"] = round(m.get("node_load5", 0), 2)
    fields["load_15m"] = round(m.get("node_load15", 0), 2)

    # Memory
    total = m.get("node_memory_MemTotal_bytes", 0)
    avail = m.get("node_memory_MemAvailable_bytes", 0)
    free = m.get("node_memory_MemFree_bytes", 0)
    buffers = m.get("node_memory_Buffers_bytes", 0)
    cached = m.get("node_memory_Cached_bytes", 0)
    swap_total = m.get("node_memory_SwapTotal_bytes", 0)
    swap_free = m.get("node_memory_SwapFree_bytes", 0)
    used = total - avail if avail > 0 else total - free - buffers - cached
    fields["memory_percent"] = round(used / total * 100, 1) if total > 0 else 0
    fields["swap_used_gb"] = round((swap_total - swap_free) / 1e9, 1)
    fields["swap_in_rate"] = 0
    fields["swap_out_rate"] = 0
    fields["mem_page_faults"] = 0

    # Disk - use first real filesystem
    total_gb = _get_first_without(m, "node_filesystem_size_bytes", "tmpfs") / 1e9
    avail_gb = _get_first_without(m, "node_filesystem_avail_bytes", "tmpfs") / 1e9
    fields["disk_total_gb"] = round(total_gb, 1)
    fields["disk_used_gb"] = round(total_gb - avail_gb, 1)
    fields["disk_percent"] = round((total_gb - avail_gb) / total_gb * 100, 1) if total_gb > 0 else 0
    fields["inode_percent"] = 0

    # Disk IO
    fields["disk_read_mbps"] = 0
    fields["disk_write_mbps"] = 0
    fields["disk_read_iops"] = 0
    fields["disk_write_iops"] = 0

    # Network
    net_in = 0
    net_out = 0
    for key, val in m.items():
        if key == "node_network_receive_bytes_total" and 'device="lo"' not in str(key):
            net_in += val
        if key == "node_network_transmit_bytes_total" and 'device="lo"' not in str(key):
            net_out += val
    fields["network_in_mbps"] = round(net_in / 1e6 * 8, 3)  # bytes → Mbps
    fields["network_out_mbps"] = round(net_out / 1e6 * 8, 3)
    fields["network_connections"] = m.get("node_netstat_Tcp_CurrEstab", 0)
    fields["net_errors_total"] = 0
    fields["net_drops_total"] = 0
    fields["net_link_speed"] = int(m.get("node_network_speed_bytes", 0)) if m.get("node_network_speed_bytes") else 0
    fields["net_link_up"] = int(m.get("node_network_up", 0)) if m.get("node_network_up") else 0
    fields["listening_ports"] = 0

    # TCP
    fields["tcp_established"] = m.get("node_netstat_Tcp_CurrEstab", 0)
    fields["tcp_timewait"] = 0
    fields["tcp_closewait"] = 0

    # System
    fields["process_count"] = 0
    fields["cpu_steal"] = 0
    fields["iowait"] = 0
    fields["context_switches"] = int(m.get("node_context_switches_total", 0)) % 10000
    fields["zombie_count"] = 0
    fields["d_state_procs"] = 0
    fields["logged_users"] = int(m.get("node_logged_in_users", 0)) if m.get("node_logged_in_users") else 0
    fields["file_handles_used"] = int(m.get("node_filefd_allocated", 0)) if m.get("node_filefd_allocated") else 0
    fields["file_handles_max"] = int(m.get("node_filefd_maximum", 0)) if m.get("node_filefd_maximum") else 0
    fields["oom_events"] = 0
    fields["kernel_errors"] = 0
    fields["ntp_offset_ms"] = 0
    fields["cpu_temp"] = 0

    # Disk health
    fields["smart_health"] = "N/A"
    fields["raid_status"] = "N/A"
    fields["smart_reallocated"] = 0
    fields["smart_temp"] = 0
    fields["smart_lifetime"] = 0

    # Service status
    fields["svc_sshd"] = 1
    fields["svc_cron"] = 1 if m.get("node_systemd_unit_state", "").count("cron") > 0 else 0
    fields["svc_docker"] = 0

    fields["top_processes"] = "[]"

    return fields


def _count_cpu_cores(m: dict) -> int:
    count = 0
    for key in m:
        if key == "node_cpu_seconds_total" and 'idle' in str(key):
            count += 1
    return count if count > 0 else 1
