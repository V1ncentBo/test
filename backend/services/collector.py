"""监控数据采集与存储服务"""
import time
import logging
logger = logging.getLogger("collector")
from influxdb_client import InfluxDBClient
from config import INFLUXDB_URL, INFLUXDB_TOKEN, INFLUXDB_ORG, INFLUXDB_BUCKET


# 端口 IP/MAC 快照缓存（状态型数据，不进 InfluxDB 时序；由 snmp_collector 周期刷新）
PORT_IPMAC_CACHE: dict = {}

# 设备最近一次成功采集的时间戳（内存），供离线检测免打 InfluxDB（P1-8）
DEVICE_LAST_SEEN: dict = {}

# 采集器自身健康指标（内存快照，供 /api/metrics/collector-health 读取，P2-10）
COLLECTOR_STATS: dict = {"snmp": {}, "node": {}, "pve": {}}


def update_port_ipmac(machine_id, ipmac_map: dict):
    PORT_IPMAC_CACHE[int(machine_id)] = ipmac_map


def get_port_ipmac(machine_id) -> dict:
    return PORT_IPMAC_CACHE.get(int(machine_id), {})


class MetricsService:
    """InfluxDB 时序数据读写服务"""

    def get_port_ipmac(self, machine_id):
        return get_port_ipmac(machine_id)

    def update_port_ipmac(self, machine_id, ipmac_map: dict):
        update_port_ipmac(machine_id, ipmac_map)

    def touch_last_seen(self, machine_id):
        DEVICE_LAST_SEEN[int(machine_id)] = time.time()

    def get_last_seen(self, machine_id):
        return DEVICE_LAST_SEEN.get(int(machine_id))


    def __init__(self):
        from influxdb_client.client.write_api import SYNCHRONOUS
        self.client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
        self.query_api = self.client.query_api()
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)

    def write_metrics(self, data: dict) -> bool:
        """写入一条监控数据 — 使用 HTTP 直接推送到 InfluxDB"""
        try:
            mid = str(data.get("machine_id", ""))
            # 字段名映射：agent 字段 -> InfluxDB 字段
            _alias = {
                "memory_total": "memory_total_gb", "memory_used": "memory_used_gb",
                "swap_total": "swap_total_gb", "swap_used": "swap_used_gb",
                "disk_total": "disk_total_gb", "disk_used": "disk_used_gb",
            }
            float_keys = ["cpu_percent", "cpu_temp", "memory_total", "memory_used",
                         "memory_percent", "swap_used", "disk_total", "disk_used",
                         "disk_percent", "disk_read_mbps", "disk_write_mbps",
                         "network_in_mbps", "network_out_mbps", "load_1m", "load_5m", "load_15m",
                         "inode_percent", "ntp_offset_ms", "iowait", "cpu_steal"]
            int_keys = ["cpu_cores", "network_connections", "uptime_seconds", "process_count",
                       "zombie_count", "logged_users", "file_handles_used", "file_handles_max",
                       "context_switches", "listening_ports",
                       "net_errors_total", "net_drops_total", "oom_events",
                       "tcp_established", "tcp_timewait", "tcp_closewait",
                       "smart_reallocated", "smart_temp", "smart_lifetime",
                       "swap_in_rate", "swap_out_rate", "kernel_errors",
                       "net_link_speed", "net_link_up", "d_state_procs",
                       "svc_sshd", "svc_cron", "svc_docker",
                       "cpu_freq_mhz", "mem_page_faults",
                       "disk_read_iops", "disk_write_iops"]
            fields = []
            for k in float_keys:
                # 注意：不能用 `data.get(k) or data.get(alias)` —— 0.0 是 falsy，
                # 会把「空载 = 0 MB/s」误判成「该字段不存在」整条丢弃，导致磁盘/网络
                # 速率图在空闲时段完全没有数据点（表现为「监测不到」）。
                v = data.get(k)
                if v is None:
                    v = data.get(_alias.get(k))
                if v is not None:
                    db_key = _alias.get(k, k)
                    fields.append(f"{db_key}={float(v)}")
            for k in int_keys:
                v = data.get(k)
                if v is not None:
                    iv = int(v)
                    # InfluxDB int64 上限为 2^63-1；个别 agent 会上报 2^63(如 file_handles_max)
                    # 触发 400 写入失败并导致整条指标丢弃、设备被误判离线，这里裁剪到上限。
                    if iv > 9223372036854775807:
                        iv = 9223372036854775807
                    fields.append(f"{k}={iv}i")
            if not fields:
                return False
            line = f"machine_metrics,machine_id={mid} " + ",".join(fields)
            self.write_api.write(INFLUXDB_BUCKET, INFLUXDB_ORG, line)
            return True
        except Exception as e:
            print(f"[MetricsService] Write error: {e}")
            return False

    def batch_write(self, metrics_list: list) -> int:
        """批量写入监控数据"""
        success = 0
        for d in metrics_list:
            if self.write_metrics(d):
                success += 1
        return success

    def write_device_metrics(self, machine_id: int, online: bool, sys_uptime: int = 0) -> bool:
        """写入网络设备(device_metrics)设备级最新指标"""
        try:
            mid = str(machine_id)
            fields = [f"online={(1 if online else 0)}i", f"sys_uptime={int(sys_uptime)}i"]
            line = f"device_metrics,machine_id={mid} " + ",".join(fields)
            self.write_api.write(INFLUXDB_BUCKET, INFLUXDB_ORG, line)
            return True
        except Exception as e:
            print(f"[MetricsService] Device write error: {e}")
            return False

    def write_port_metrics(self, machine_id: int, port_index: int, port_name: str,
                           data: dict) -> bool:
        """写入端口级(port_metrics)指标。tag: machine_id + port_index + port_name"""
        try:
            mid = str(machine_id)
            pidx = str(port_index)
            # port_name 含特殊字符需转义（空格->\ ，逗号->\, 等）
            safe_name = port_name.replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")
            fields = [
                f"ifOperStatus={int(data.get('ifOperStatus', 0))}i",
                f"ifSpeed={int(data.get('ifSpeed', 0))}i",
                f"ifInOctets={int(data.get('ifInOctets', 0))}i",
                f"ifOutOctets={int(data.get('ifOutOctets', 0))}i",
                f"in_mbps={float(data.get('in_mbps', 0.0))}",
                f"out_mbps={float(data.get('out_mbps', 0.0))}",
                f"util_in={float(data.get('util_in', 0.0))}",
                f"util_out={float(data.get('util_out', 0.0))}",
                f"in_errors={int(data.get('in_errors', 0))}i",
                f"out_errors={int(data.get('out_errors', 0))}i",
                f"in_discards={int(data.get('in_discards', 0))}i",
                f"out_discards={int(data.get('out_discards', 0))}i",
            ]
            line = f"port_metrics,machine_id={mid},port_index={pidx},port_name={safe_name} " + ",".join(fields)
            self.write_api.write(INFLUXDB_BUCKET, INFLUXDB_ORG, line)
            return True
        except Exception as e:
            print(f"[MetricsService] Port write error: {e}")
            return False

    def write_ports_batch(self, machine_id: int, ports: list) -> int:
        """批量写入端口级指标：ports 为 [(port_index, port_name, data), ...]。
        一次性 write_api.write 多行 line-protocol，将 52 端口 × 每端口 1 次 HTTP 写降到 1 次。
        """
        try:
            mid = str(machine_id)
            lines = []
            for port_index, port_name, data in ports:
                pidx = str(port_index)
                safe_name = str(port_name).replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")
                fields = [
                    f"ifOperStatus={int(data.get('ifOperStatus', 0))}i",
                    f"ifSpeed={int(data.get('ifSpeed', 0))}i",
                    f"ifInOctets={int(data.get('ifInOctets', 0))}i",
                    f"ifOutOctets={int(data.get('ifOutOctets', 0))}i",
                    f"in_mbps={float(data.get('in_mbps', 0.0))}",
                    f"out_mbps={float(data.get('out_mbps', 0.0))}",
                    f"util_in={float(data.get('util_in', 0.0))}",
                    f"util_out={float(data.get('util_out', 0.0))}",
                    f"in_errors={int(data.get('in_errors', 0))}i",
                    f"out_errors={int(data.get('out_errors', 0))}i",
                    f"in_discards={int(data.get('in_discards', 0))}i",
                    f"out_discards={int(data.get('out_discards', 0))}i",
                ]
                lines.append(f"port_metrics,machine_id={mid},port_index={pidx},port_name={safe_name} " + ",".join(fields))
            if lines:
                self.write_api.write(INFLUXDB_BUCKET, INFLUXDB_ORG, "\n".join(lines))
            return len(lines)
        except Exception as e:
            print(f"[MetricsService] Ports batch write error: {e}")
            return 0

    def write_collector_stats(self, collector_name: str, **fields) -> bool:
        """写入采集器自身健康指标(collector_metrics)，使平台可观测。
        fields 示例：cycle_devices, success, failed, duration_sec, write_fail
        """
        try:
            flds = []
            for k, v in fields.items():
                if isinstance(v, int):
                    flds.append(f"{k}={int(v)}i")
                else:
                    flds.append(f"{k}={float(v)}")
            if not flds:
                return False
            line = f"collector_metrics,collector={collector_name} " + ",".join(flds)
            self.write_api.write(INFLUXDB_BUCKET, INFLUXDB_ORG, line)
            return True
        except Exception as e:
            print(f"[MetricsService] Collector stats write error: {e}")
            return False

    def query_port_latest(self, machine_id: int) -> dict:
        """查询某设备各端口最新状态（用于列表摘要/详情页端口表）。返回 {port_index: {...}}"""
        query = f'''from(bucket: "{INFLUXDB_BUCKET}")
|> range(start: -15m)
|> filter(fn: (r) => r["_measurement"] == "port_metrics")
|> filter(fn: (r) => r["machine_id"] == "{machine_id}")
|> last()
|> group(columns: ["port_index", "_field"])'''
        try:
            result = self.query_api.query(query=query)
            ports = {}
            for table in result:
                for record in table.records:
                    pidx = record.values.get("port_index", "")
                    if pidx not in ports:
                        ports[pidx] = {"port_index": pidx, "port_name": record.values.get("port_name", "")}
                    ports[pidx][record.get_field()] = record.get_value()
            return ports
        except Exception as e:
            print(f"[MetricsService] Query port latest error: {e}")
            return {}

    def query_port_history(self, machine_id: int, port_index: str,
                           start: str = "-24h") -> dict:
        """查询某端口进出速率历史曲线。返回 {"in":[...], "out":[...]}"""
        query = f'''from(bucket: "{INFLUXDB_BUCKET}")
|> range(start: {start})
|> filter(fn: (r) => r["_measurement"] == "port_metrics")
|> filter(fn: (r) => r["machine_id"] == "{machine_id}")
|> filter(fn: (r) => r["port_index"] == "{port_index}")
|> filter(fn: (r) => r["_field"] == "in_mbps" or r["_field"] == "out_mbps")
|> aggregateWindow(every: 1m, fn: mean, createEmpty: false)'''
        try:
            result = self.query_api.query(query=query)
            series = {"in": [], "out": []}
            for table in result:
                for record in table.records:
                    f = record.get_field()
                    if f == "in_mbps":
                        series["in"].append({"time": record.get_time().isoformat(), "value": round(record.get_value() or 0, 2)})
                    elif f == "out_mbps":
                        series["out"].append({"time": record.get_time().isoformat(), "value": round(record.get_value() or 0, 2)})
            return series
        except Exception as e:
            print(f"[MetricsService] Query port history error: {e}")
            return {"in": [], "out": []}

    def query_port_errors_history(self, machine_id: int, port_index: str,
                                  start: str = "-24h") -> dict:
        """查询某端口错包/丢包历史曲线。返回 {"in_errors":[...], "out_errors":[...],
        "in_discards":[...], "out_discards":[...]}"""
        fields = ["in_errors", "out_errors", "in_discards", "out_discards"]
        flt = " or ".join([f'r["_field"] == "{f}"' for f in fields])
        query = f'''from(bucket: "{INFLUXDB_BUCKET}")
|> range(start: {start})
|> filter(fn: (r) => r["_measurement"] == "port_metrics")
|> filter(fn: (r) => r["machine_id"] == "{machine_id}")
|> filter(fn: (r) => r["port_index"] == "{port_index}")
|> filter(fn: (r) => {flt})
|> aggregateWindow(every: 1m, fn: mean, createEmpty: false)'''
        try:
            result = self.query_api.query(query=query)
            series = {f: [] for f in fields}
            for table in result:
                for record in table.records:
                    f = record.get_field()
                    if f in series:
                        series[f].append({"time": record.get_time().isoformat(),
                                          "value": int(record.get_value() or 0)})
            return series
        except Exception as e:
            print(f"[MetricsService] Query port errors history error: {e}")
            return {f: [] for f in fields}

    # ===== VM 指标 (PVE 虚拟化，measurement=vm_metrics) =====
    def write_vm_metrics(self, machine_id: int, vm_id, vm_name: str, data: dict) -> bool:
        """写入单台 PVE 虚拟机的指标。tag: machine_id + vm_id + vm_name。
        field: cpu_percent, cpu_cores(i), memory_percent, memory_used_bytes(i),
        memory_total_bytes(i), disk_bytes(i), status(i), uptime_seconds(i)。
        """
        try:
            mid = str(machine_id)
            safe_name = str(vm_name).replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")
            fields = [
                f"cpu_percent={float(data.get('cpu_percent', 0))}",
                f"cpu_cores={int(data.get('cpu_cores', 0))}i",
                f"memory_percent={float(data.get('memory_percent', 0))}",
                f"memory_used_bytes={int(data.get('memory_used_bytes', 0))}i",
                f"memory_total_bytes={int(data.get('memory_total_bytes', 0))}i",
                f"disk_bytes={int(data.get('disk_bytes', 0))}i",
                f"disk_total_bytes={int(data.get('disk_total_bytes', 0))}i",
                f"disk_percent={float(data.get('disk_percent', 0))}",
                f"network_in_mbps={float(data.get('network_in_mbps', 0))}",
                f"network_out_mbps={float(data.get('network_out_mbps', 0))}",
                f"status={int(data.get('status', 0))}i",
                f"uptime_seconds={int(data.get('uptime_seconds', 0))}i",
            ]
            line = f"vm_metrics,machine_id={mid},vm_id={int(vm_id)},vm_name={safe_name} " + ",".join(fields)
            self.write_api.write(INFLUXDB_BUCKET, INFLUXDB_ORG, line)
            return True
        except Exception as e:
            print(f"[MetricsService] VM metrics write error: {e}")
            return False

    def query_vm_latest(self, machine_id: int, vm_id) -> dict:
        """某 VM 最近一次指标快照（device_ports 同类，用于详情页信息卡）。"""
        try:
            result = self.query_api.query(query=f'''from(bucket: "{INFLUXDB_BUCKET}")
|> range(start: -15m)
|> filter(fn: (r) => r["_measurement"] == "vm_metrics")
|> filter(fn: (r) => r["machine_id"] == "{machine_id}")
|> filter(fn: (r) => r["vm_id"] == "{vm_id}")
|> last()''')
            fields = {}
            for table in result:
                for record in table.records:
                    fields[record.get_field()] = record.get_value()
            return fields
        except Exception as e:
            print(f"[MetricsService] Query VM latest error: {e}")
            return {}

    def query_vm_history(self, machine_id: int, vm_id, metric: str, start: str = "-24h") -> list:
        """某 VM 某指标的历史曲线。返回 [{time, value}]。"""
        query = f'''from(bucket: "{INFLUXDB_BUCKET}")
|> range(start: {start})
|> filter(fn: (r) => r["_measurement"] == "vm_metrics")
|> filter(fn: (r) => r["machine_id"] == "{machine_id}")
|> filter(fn: (r) => r["vm_id"] == "{vm_id}")
|> filter(fn: (r) => r["_field"] == "{metric}")
|> aggregateWindow(every: 1m, fn: mean, createEmpty: false)'''
        try:
            result = self.query_api.query(query=query)
            points = []
            for table in result:
                for record in table.records:
                    points.append({
                        "time": record.get_time().isoformat(),
                        "value": round(record.get_value() or 0, 2)
                    })
            return points
        except Exception as e:
            print(f"[MetricsService] Query VM history error: {e}")
            return []

    def query_metrics(self, machine_id: int, metric_name: str,
                      start: str = "-24h", end: str = "now", step: str = "auto") -> list:
        """查询某个指标的历史数据"""
        flux_fields = {
            "cpu_percent": "cpu_percent",
            "memory_percent": "memory_percent",
            "disk_percent": "disk_percent",
            "network_in_mbps": "network_in_mbps",
            "network_out_mbps": "network_out_mbps",
            "load_1m": "load_1m",
            "disk_read_mbps": "disk_read_mbps",
            "disk_write_mbps": "disk_write_mbps",
        }
        if metric_name not in flux_fields:
            return []
        field = flux_fields[metric_name]

        # 自动选择聚合窗口
        if step == "auto":
            if start == "-1h":
                step = "10m"
            elif start in ("-7d", "-7d"):
                step = "6h"
            else:
                step = "30m"

        query = f'''from(bucket: "{INFLUXDB_BUCKET}")
|> range(start: {start})
|> filter(fn: (r) => r["machine_id"] == "{machine_id}")
|> filter(fn: (r) => r["_field"] == "{field}")
|> aggregateWindow(every: {step}, fn: mean, createEmpty: false)'''

        try:
            result = self.query_api.query(query=query)
            points = []
            for table in result:
                for record in table.records:
                    points.append({
                        "time": record.get_time().isoformat(),
                        "value": round(record.get_value() or 0, 2)
                    })
            return points
        except Exception as e:
            print(f"[MetricsService] Query error: {e}")
            return []

    def query_latest(self, machine_id: int) -> dict | None:
        """查询某设备最新一条监控数据（machine_metrics 优先，vm_metrics 回退）"""
        def _q(measurement):
            query = f'''from(bucket: "{INFLUXDB_BUCKET}")
|> range(start: -2h)
|> filter(fn: (r) => r["_measurement"] == "{measurement}")
|> filter(fn: (r) => r["machine_id"] == "{machine_id}")
|> last()'''
            try:
                result = self.query_api.query(query=query)
                fields = {}
                for table in result:
                    for record in table.records:
                        fields[record.get_field()] = record.get_value()
                return fields
            except Exception as e:
                print(f"[MetricsService] Query latest error: {e}")
                return {}
        host = _q("machine_metrics")
        if host:
            return host
        vm = _q("vm_metrics")
        return vm if vm else None

    def query_all_latest(self) -> dict:
        """查询所有设备最新指标（用于首页大屏）。machine_metrics 优先，vm_metrics 补齐缺失字段。"""
        def _q(measurement):
            query = f'''from(bucket: "{INFLUXDB_BUCKET}")
|> range(start: -2h)
|> filter(fn: (r) => r["_measurement"] == "{measurement}")
|> filter(fn: (r) => r["_field"] == "cpu_percent" or r["_field"] == "memory_percent"
           or r["_field"] == "disk_percent" or r["_field"] == "load_1m"
           or r["_field"] == "network_in_mbps" or r["_field"] == "network_out_mbps")
|> last()
|> group(columns: ["machine_id", "_field"])'''
            try:
                result = self.query_api.query(query=query)
                latest = {}
                for table in result:
                    for record in table.records:
                        mid = record.values.get("machine_id", "")
                        field = record.get_field()
                        if mid not in latest:
                            latest[mid] = {}
                        latest[mid][field] = round(record.get_value() or 0, 2)
                return latest
            except Exception as e:
                print(f"[MetricsService] Query all latest error: {e}")
                return {}

        host = _q("machine_metrics")
        vm = _q("vm_metrics")
        # 合并：machine_metrics 优先；VM 子机若无 host 指标，则用 vm_metrics 补齐，避免整行丢失
        merged = {}
        for mid, fields in vm.items():
            merged[mid] = dict(fields)
        for mid, fields in host.items():
            if mid not in merged:
                merged[mid] = {}
            merged[mid].update(fields)
        return merged

    def query_range(self, machine_id: int, start: str, end: str = "now") -> dict:
        """查询时间范围内的所有指标数据"""
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: {start}, stop: {end})
          |> filter(fn: (r) => r["_measurement"] == "machine_metrics")
          |> filter(fn: (r) => r["machine_id"] == "{machine_id}")
          |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
        '''
        try:
            result = self.query_api.query(query=query)
            series = {}
            for table in result:
                for record in table.records:
                    field = record.get_field()
                    if field not in series:
                        series[field] = []
                    series[field].append({
                        "time": record.get_time().isoformat(),
                        "value": round(record.get_value() or 0, 2)
                    })
            return series
        except Exception as e:
            print(f"[MetricsService] Query range error: {e}")
            return {}

    def write_db_metrics(self, instance_id: int, db_type: str, fields: dict) -> bool:
        """写入数据库实例监控指标(db_metrics)。tag: instance_id + type。只读采集结果落库。"""
        try:
            iid = str(instance_id)
            safe_type = str(db_type).replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")
            flds = []
            for k, v in fields.items():
                if v is None or isinstance(v, bool):
                    continue
                if isinstance(v, int):
                    flds.append(f"{k}={int(v)}i")
                elif isinstance(v, float):
                    flds.append(f"{k}={float(v)}")
                else:
                    safe = str(v).replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")
                    flds.append(f'{k}="{safe}"')
            if not flds:
                return False
            line = f"db_metrics,instance_id={iid},type={safe_type} " + ",".join(flds)
            self.write_api.write(INFLUXDB_BUCKET, INFLUXDB_ORG, line)
            return True
        except Exception as e:
            body = getattr(e, "body", "") or ""
            print(f"[MetricsService] DB metrics write error: {e!r} | body={body} | line={line}")
            return False

    def query_db_history(self, instance_id: int, field: str, start: str = "-1h") -> list:
        """查询某数据库实例某指标的历史曲线(db_metrics)。返回 [{time, value}]。"""
        query = f'''from(bucket: "{INFLUXDB_BUCKET}")
|> range(start: {start})
|> filter(fn: (r) => r["_measurement"] == "db_metrics")
|> filter(fn: (r) => r["instance_id"] == "{instance_id}")
|> filter(fn: (r) => r["_field"] == "{field}")
|> aggregateWindow(every: 1m, fn: mean, createEmpty: false)'''
        try:
            result = self.query_api.query(query=query)
            points = []
            for table in result:
                for record in table.records:
                    points.append({
                        "time": record.get_time().isoformat(),
                        "value": round(record.get_value() or 0, 2)
                    })
            return points
        except Exception as e:
            print(f"[MetricsService] Query DB history error: {e}")
            return []

    def close(self):
        self.client.close()


metrics_service = MetricsService()
