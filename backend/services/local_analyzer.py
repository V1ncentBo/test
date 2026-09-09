"""内置智能分析引擎 - 不依赖外部大模型，基于监控数据规则引擎"""
import json
from datetime import datetime, timedelta


class LocalAnalyzer:
    """本地分析引擎：基于阈值规则 + 趋势计算，生成运维分析报告"""

    @staticmethod
    def analyze(machine_info: dict, metrics_data: dict, alerts: list,
                analysis_type: str = "full", query: str = None,
                history_data: dict = None) -> dict:
        """分析入口"""
        name = machine_info.get("name", "未知设备")
        ip = machine_info.get("ip", "")
        cpu = metrics_data.get("cpu_percent", 0) or 0
        mem = metrics_data.get("memory_percent", 0) or 0
        disk = metrics_data.get("disk_percent", 0) or 0
        load = metrics_data.get("load_1m", 0) or 0
        cores = metrics_data.get("cpu_cores", 1) or 1
        net_in = metrics_data.get("network_in_mbps", 0) or 0
        net_out = metrics_data.get("network_out_mbps", 0) or 0
        disk_r = metrics_data.get("disk_read_mbps", 0) or 0
        disk_w = metrics_data.get("disk_write_mbps", 0) or 0
        temp = metrics_data.get("cpu_temp", 0) or 0
        uptime_s = metrics_data.get("uptime_seconds", 0) or 0
        mem_total = metrics_data.get("memory_total_gb", 0) or 0
        disk_total = metrics_data.get("disk_total_gb", 0) or 0

        uptime_days = uptime_s / 86400
        load_ratio = load / cores if cores > 0 else 0

        # 构建完整分析
        parts = []

        # 1. 设备运行概况
        overview = f"""设备 **{name}**（{ip}）当前运行状态：

| 指标 | 当前值 | 状态 |
|------|--------|------|
| CPU 使用率 | {cpu}% ({cores}核) | {_status_icon(cpu, 60, 80)} |
| 内存使用率 | {mem}% ({mem_total}GB 总量) | {_status_icon(mem, 70, 90)} |
| 磁盘使用率 | {disk}% ({disk_total}GB 总量) | {_status_icon(disk, 70, 85)} |
| 系统负载 | {load} (负载/核心比: {load_ratio:.2f}) | {_status_icon(load_ratio * 100, 70, 100)} |
| 网络流量 | 入 {net_in} Mbps / 出 {net_out} Mbps | — |
| 磁盘 IO | 读 {disk_r} MB/s / 写 {disk_w} MB/s | — |
| CPU 温度 | {temp}°C | {_status_icon(temp, 65, 80)} |
| 运行时长 | {uptime_days:.1f} 天 | — |

**综合评估**：{_overall_assessment(cpu, mem, disk, load_ratio, temp)}"""

        parts.append(("设备运行概况", overview))

        # 2. 异常诊断
        issues = []
        if cpu >= 80:
            issues.append(f"- **CPU 高负载**：使用率 {cpu}%，可能原因：进程死循环、并发请求激增。建议：`top` 或 `htop` 检查占用 CPU 最高的进程，必要时 kill 或优化代码。")
        if mem >= 85:
            issues.append(f"- **内存不足**：使用率 {mem}%，剩余仅 {mem_total * (1 - mem/100):.1f}GB。可能原因：内存泄漏、缓存未释放。建议：检查大内存进程 `ps aux --sort=-%mem | head`，考虑增加 swap 或扩容。")
        if disk >= 80:
            issues.append(f"- **磁盘空间紧张**：使用率 {disk}%，剩余 {(disk_total * (1 - disk/100)):.1f}GB。建议：清理日志 `find /var/log -name '*.log' -mtime +30 -delete`，检查大文件 `du -sh /* 2>/dev/null | sort -rh | head -10`。")
        if temp >= 75:
            issues.append(f"- **CPU 温度偏高**：{temp}°C。建议：检查散热风扇、清理灰尘、改善机房通风。")
        if load_ratio > 1.5:
            issues.append(f"- **系统负载过高**：负载/核心比 {load_ratio:.2f} > 1.5，CPU 可能成为瓶颈。建议：优化应用、增加核心数或分流。")
        if disk_r > 100 or disk_w > 100:
            issues.append(f"- **磁盘 IO 较高**：读 {disk_r}MB/s, 写 {disk_w}MB/s。建议：检查是否有大量读写操作，考虑使用 SSD 或优化数据库查询。")
        if uptime_days > 365:
            issues.append(f"- **运行时间过长**：已运行 {uptime_days:.0f} 天，建议安排维护窗口重启以加载最新内核补丁。")

        if alerts:
            issues.append(f"\n**近期告警记录**（{len(alerts)} 条）：")
            for a in alerts[:5]:
                issues.append(f"  - [{a.get('alert_level', '?')}] {a.get('message', '')}")

        diagnosis = "\n".join(issues) if issues else "当前无明显异常，系统运行正常。"

        parts.append(("现存异常与故障", diagnosis))

        # 3. 趋势预测
        predictions = []
        cpu_trend = _calc_trend(history_data, "cpu_percent") if history_data else None
        mem_trend = _calc_trend(history_data, "memory_percent") if history_data else None
        disk_trend = _calc_trend(history_data, "disk_percent") if history_data else None

        if cpu_trend and cpu_trend > 1.5:
            predictions.append(f"- CPU 使用率呈上升趋势（增长率 {cpu_trend*100-100:.0f}%/周期），预计 {(80-cpu)/max(cpu_trend-1, 0.01)*10:.0f} 分钟后可能触发告警。")
        if mem_trend and mem_trend > 1.3:
            predictions.append(f"- 内存使用率持续增长（增长率 {mem_trend*100-100:.0f}%/周期），需关注是否内存泄漏。")
        if disk_trend and disk_trend > 1.01:
            predictions.append(f"- 磁盘使用率缓慢增长，按当前速率预计 {(85-disk)/max(disk_trend-1, 0.001)/144:.0f} 天后达到 85% 告警阈值。")
        if disk > 60:
            predictions.append(f"- 磁盘使用率 {disk}%，建议提前规划扩容或清理策略。")

        prediction = "\n".join(predictions) if predictions else "基于当前数据趋势，未检测到明显的资源耗尽风险。建议持续监控。"

        parts.append(("潜在风险预测", prediction))

        # 4. 优化建议
        optimizations = []
        if cpu < 20 and mem < 40:
            optimizations.append(f"- 💡 **资源利用率偏低**：CPU {cpu}%, 内存 {mem}%。可考虑在此设备上部署更多服务，提高资源利用率。")
        if cpu > 60:
            optimizations.append(f"- ⚙️ **CPU 调优**：检查是否有可优化的计算密集型任务，考虑启用多线程/异步处理。")
        if mem > 60:
            optimizations.append(f"- ⚙️ **内存优化**：检查应用是否存在内存泄漏，配置合理的 JVM/进程内存限制。")
        if disk_r > 50 or disk_w > 50:
            optimizations.append(f"- 📊 **IO 优化**：磁盘读写繁忙，建议使用缓存层（Redis）减少直接磁盘访问，或升级 SSD。")
        if not optimizations:
            optimizations.append("- ✅ 当前资源配置合理，无明显优化项。建议保持当前配置并持续监控。")

        optimization = "\n".join(optimizations)
        parts.append(("运维优化建议", optimization))

        # 根据分析类型过滤输出
        if analysis_type == "diagnosis":
            overview = ""  # 诊断模式不输出概况
            prediction = ""
            optimization = ""
        elif analysis_type == "prediction":
            overview = ""
            diagnosis = ""
            optimization = ""
        elif analysis_type == "optimization":
            overview = ""
            diagnosis = ""
            prediction = ""
        # "full" 保持全部

        # 构建返回
        result = {"success": True, "raw": ""}
        if overview:
            result["raw"] += f"## 设备运行概况\n{overview}\n\n"
        if diagnosis:
            result["raw"] += f"## 现存异常与故障\n{diagnosis}\n\n"
        if prediction:
            result["raw"] += f"## 潜在风险预测\n{prediction}\n\n"
        if optimization:
            result["raw"] += f"## 运维优化建议\n{optimization}\n\n"

        result["overview"] = overview
        result["diagnosis"] = diagnosis
        result["prediction"] = prediction
        result["optimization"] = optimization

        return result

    @staticmethod
    def chat(context: dict, user_query: str) -> str:
        """本地智能问答"""
        machine = context.get("machine", {})
        metrics = context.get("metrics", {})
        name = machine.get("name", "未知设备")
        cpu = metrics.get("cpu_percent", 0) or 0
        mem = metrics.get("memory_percent", 0) or 0
        disk = metrics.get("disk_percent", 0) or 0

        query_lower = user_query.lower()

        if "异常" in user_query or "故障" in user_query or "问题" in user_query:
            issues = []
            if cpu > 80:
                issues.append(f"CPU 使用率 {cpu}% 偏高")
            if mem > 85:
                issues.append(f"内存使用率 {mem}% 偏高")
            if disk > 80:
                issues.append(f"磁盘使用率 {disk}% 偏高")
            return f"设备 **{name}** 当前状态：" + ("\n".join(f"- {i}" for i in issues) if issues else "\n- 未检测到明显异常，系统运行正常。")

        if "卡顿" in user_query or "慢" in user_query or "性能" in user_query:
            return f"""设备 **{name}** 性能分析：
- CPU: {cpu}%{"（偏高，可能导致卡顿）" if cpu > 70 else "（正常）"}
- 内存: {mem}%{"（不足，可能影响性能）" if mem > 80 else "（正常）"}
- 磁盘: {disk}%{"（空间紧张）" if disk > 80 else "（正常）"}
- 建议：{"优化占用资源的进程" if cpu > 70 or mem > 80 else "当前资源充足，卡顿可能由网络或应用层引起"}"""

        if "cpu" in query_lower:
            return f"设备 {name} CPU 使用率 {cpu}%，{cores_desc(metrics.get('cpu_cores', 0))}，负载 {metrics.get('load_1m', '?')}。状态：{'正常 ✅' if cpu < 70 else '偏高 ⚠️' if cpu < 90 else '危险 🚨'}"

        if "内存" in query_lower or "memory" in query_lower:
            return f"设备 {name} 内存使用率 {mem}%，总量 {metrics.get('memory_total_gb', '?')}GB，已用 {metrics.get('memory_used_gb', '?')}GB。状态：{'正常 ✅' if mem < 80 else '偏高 ⚠️' if mem < 90 else '危险 🚨'}"

        if "磁盘" in query_lower or "硬盘" in query_lower or "disk" in query_lower:
            return f"设备 {name} 磁盘使用率 {disk}%，总量 {metrics.get('disk_total_gb', '?')}GB，已用 {metrics.get('disk_used_gb', '?')}GB。IO: 读 {metrics.get('disk_read_mbps', '?')}MB/s, 写 {metrics.get('disk_write_mbps', '?')}MB/s。"

        # 默认回答
        return f"""设备 **{name}** ({machine.get('ip', '')}) 当前状态：
- CPU: {cpu}% | 内存: {mem}% | 磁盘: {disk}%
- 负载: {metrics.get('load_1m', '?')} | 温度: {metrics.get('cpu_temp', '?')}°C
- 运行时长: {_format_uptime(metrics.get('uptime_seconds', 0))}

**综合判断**：{_overall_assessment(cpu, mem, disk, (metrics.get('load_1m', 0) or 0) / max(metrics.get('cpu_cores', 1), 1), metrics.get('cpu_temp', 0) or 0)}"""

    @staticmethod
    def generate_report(all_machines: list, period: str = "daily") -> str:
        """生成本地运维报告"""
        total = len(all_machines)
        online = sum(1 for m in all_machines if m.get("status") == "online")
        cpu_vals = [m.get("cpu", 0) for m in all_machines if m.get("cpu")]
        mem_vals = [m.get("memory", 0) for m in all_machines if m.get("memory")]
        alert_count = sum(m.get("alert_count", 0) for m in all_machines)

        avg_cpu = sum(cpu_vals) / len(cpu_vals) if cpu_vals else 0
        avg_mem = sum(mem_vals) / len(mem_vals) if mem_vals else 0

        report = f"""# {_period_label(period)}运维报告

## 运行概况
{total} 台设备，在线 {online} 台。CPU 平均 {avg_cpu:.1f}%，内存平均 {avg_mem:.1f}%。

## 异常与告警
"""
        for m in all_machines:
            if m.get("alert_count", 0) > 0:
                report += f"- **{m['name']}**（{m['ip']}）：{m['alert_count']} 条告警\n"
        if alert_count == 0:
            report += "本期运行正常，无异常告警。\n"

        report += f"""
## 资源分析
"""
        for m in all_machines:
            cpu = m.get("cpu", 0)
            mem = m.get("memory", 0)
            status = "正常" if cpu < 60 and mem < 60 else ("关注" if cpu < 80 and mem < 80 else "告警")
            report += f"- {m['name']}：CPU {cpu}% / MEM {mem}%（{status}）\n"

        report += f"""
## 优化建议
"""
        high_cpu = [m for m in all_machines if m.get("cpu", 0) > 60]
        high_mem = [m for m in all_machines if m.get("memory", 0) > 60]
        if high_cpu or high_mem:
            if high_cpu: report += f"- {len(high_cpu)} 台 CPU > 60%，建议检查高负载进程或规划扩容\n"
            if high_mem: report += f"- {len(high_mem)} 台内存 > 60%，建议排查内存占用大户\n"
        else:
            report += "资源充裕，暂无优化建议。\n"
        if high_mem:
            report += f"- {len(high_mem)} 台设备内存使用率 > 60%，建议排查内存泄漏\n"
        if not high_cpu and not high_mem:
            report += "- 当前所有设备资源使用在合理范围内，无需优化。\n"

        report += f"""
## 下阶段关注事项
- 持续监控资源使用趋势，提前发现瓶颈
- 定期检查告警日志，及时处理异常事件
- 建议配置自动化告警通知（邮件/钉钉/微信）
"""
        return report


# ===== 辅助函数 =====

def _status_icon(value: float, warn: float, danger: float) -> str:
    if value >= danger: return "🔴 危险"
    if value >= warn: return "🟡 警告"
    return "🟢 正常"


def _overall_assessment(cpu: float, mem: float, disk: float, load_ratio: float, temp: float = 0) -> str:
    issues = []
    if cpu >= 80: issues.append("CPU高负载")
    if mem >= 85: issues.append("内存不足")
    if disk >= 80: issues.append("磁盘紧张")
    if load_ratio > 1.5: issues.append("系统过载")
    if temp >= 75: issues.append("温度偏高")

    if not issues:
        return "系统运行正常，各项指标均在合理范围内。✅"
    return f"存在 {len(issues)} 项风险：{', '.join(issues)}。⚠️"


def _calc_trend(history: dict, metric: str) -> float | None:
    """简单趋势计算：最近值 / 较早值"""
    if not history or metric not in history:
        return None
    points = history[metric]
    if len(points) < 5:
        return None
    recent = sum(p["value"] for p in points[-3:]) / 3
    earlier = sum(p["value"] for p in points[:3]) / 3
    if earlier == 0:
        return None
    return recent / earlier


def _format_uptime(seconds: int) -> str:
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if days: parts.append(f"{days}天")
    if hours: parts.append(f"{hours}小时")
    if minutes: parts.append(f"{minutes}分钟")
    return "".join(parts) if parts else "< 1分钟"


def _period_label(p: str) -> str:
    return {"daily": "日", "weekly": "周", "monthly": "月"}.get(p, p)


def cores_desc(cores: int) -> str:
    return f"{cores}核{'物理机' if cores >= 8 else '虚拟机'}"
