"""
报表 HTML 模板
═══════════════════════
日报：简洁版 — 概览卡片 + 指标快照 + 告警列表
周报：标准版 — 趋势图 + 告警统计 + 健康评分 + AI 分析
月报：豪华版 — 热力图 + 容量规划 + SLA + 成本优化 + AI 深度分析
"""
from datetime import datetime
from typing import Dict, Any


class ReportTemplates:
    """报表模板渲染器"""

    # ════════════════════════════════════════════════════════
    #  公共 CSS
    # ════════════════════════════════════════════════════════

    BASE_CSS = """
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
            background: #f5f7fa; color: #303133; line-height: 1.6;
        }
        .container { max-width: 1000px; margin: 0 auto; padding: 20px; }
        .header {
            background: linear-gradient(135deg, #409EFF 0%, #337ECC 100%);
            color: white; padding: 30px 40px; border-radius: 12px; margin-bottom: 24px;
            box-shadow: 0 4px 16px rgba(64, 158, 255, 0.3);
        }
        .header h1 { font-size: 28px; font-weight: 600; margin-bottom: 8px; }
        .header .meta { font-size: 14px; opacity: 0.9; }
        .section {
            background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.06);
        }
        .section h2 {
            font-size: 20px; font-weight: 600; color: #303133;
            padding-bottom: 12px; border-bottom: 2px solid #EBEEF5; margin-bottom: 16px;
            display: flex; align-items: center; gap: 8px;
        }
        .section h2 .icon { font-size: 22px; }
        .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }
        .card {
            background: linear-gradient(135deg, #f8f9ff 0%, #f0f2ff 100%);
            border-radius: 10px; padding: 20px; text-align: center;
            border: 1px solid #e8ecf4;
        }
        .card .label { font-size: 13px; color: #909399; margin-bottom: 6px; }
        .card .value { font-size: 32px; font-weight: 700; color: #303133; }
        .card .sub { font-size: 12px; color: #909399; margin-top: 4px; }
        .card.warning { background: linear-gradient(135deg, #fff8f0 0%, #fff3e6 100%); border-color: #ffd6a5; }
        .card.warning .value { color: #E6A23C; }
        .card.danger { background: linear-gradient(135deg, #fff0f0 0%, #ffe6e6 100%); border-color: #ffb3b3; }
        .card.danger .value { color: #F56C6C; }
        .card.success { background: linear-gradient(135deg, #f0fff4 0%, #e6ffe6 100%); border-color: #b3e6b3; }
        .card.success .value { color: #67C23A; }

        table { width: 100%; border-collapse: collapse; font-size: 14px; }
        th { background: #f5f7fa; padding: 12px; text-align: left; font-weight: 600; color: #606266; border-bottom: 2px solid #EBEEF5; }
        td { padding: 12px; border-bottom: 1px solid #EBEEF5; color: #303133; }
        tr:hover { background: #f5f7fa; }

        .chart-container { width: 100%; height: 350px; margin: 16px 0; background: #fafafa; border-radius: 8px; }
        .tag { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; }
        .tag-online { background: #e6ffe6; color: #67C23A; }
        .tag-offline { background: #ffe6e6; color: #F56C6C; }
        .tag-critical { background: #ffe6e6; color: #F56C6C; }
        .tag-warning { background: #fff3e6; color: #E6A23C; }
        .tag-info { background: #e6f0ff; color: #409EFF; }

        .health-bar { height: 8px; background: #EBEEF5; border-radius: 4px; overflow: hidden; margin-top: 4px; }
        .health-bar .fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
        .fill-A { background: #67C23A; }
        .fill-B { background: #409EFF; }
        .fill-C { background: #E6A23C; }
        .fill-D { background: #F56C6C; }

        .ai-box {
            background: linear-gradient(135deg, #f0f8ff 0%, #e8f4fd 100%);
            border: 1px solid #b3d8ff; border-radius: 10px; padding: 20px;
            margin-top: 16px;
        }
        .ai-box .ai-label { font-size: 13px; color: #409EFF; font-weight: 600; margin-bottom: 8px; }
        .ai-box p { font-size: 14px; color: #303133; line-height: 1.8; }
        .footer { text-align: center; padding: 24px; color: #909399; font-size: 12px; }
    </style>
    """

    # ════════════════════════════════════════════════════════
    #  日报模板 — 简洁高效
    # ════════════════════════════════════════════════════════

    @staticmethod
    def render_daily(report: dict) -> str:
        """渲染日报 HTML"""
        ov = report.get("overview", {})
        ms = report.get("metrics_snapshot", {})
        alerts = report.get("alerts", [])
        devices = report.get("device_status", [])
        ai = report.get("ai_summary", "")

        # 卡片数据
        cpu = ms.get("cpu", {})
        mem = ms.get("memory", {})
        disk = ms.get("disk", {})

        cpu_class = "danger" if cpu.get("avg", 0) > 80 else ("warning" if cpu.get("avg", 0) > 60 else "")
        mem_class = "danger" if mem.get("avg", 0) > 85 else ("warning" if mem.get("avg", 0) > 70 else "")
        disk_class = "danger" if disk.get("avg", 0) > 85 else ("warning" if disk.get("avg", 0) > 70 else "")

        alert_class = "danger" if ov.get("total_alerts", 0) > 0 else "success"

        # 设备行
        device_rows = ""
        for d in devices:
            status_tag = "tag-online" if d.get("status") == "online" else "tag-offline"
            device_rows += f"""
            <tr>
                <td><strong>{d.get('name', '-')}</strong></td>
                <td>{d.get('ip', '-')}</td>
                <td>{d.get('type', '-')}</td>
                <td><span class="tag {status_tag}">{d.get('status', '-')}</span></td>
            </tr>"""

        # 告警行
        alert_rows = ""
        for a in alerts[:10]:
            sev_class = f"tag-{a.get('severity', 'info')}"
            alert_rows += f"""
            <tr>
                <td>{a.get('machine_name', '-')}</td>
                <td><span class="tag {sev_class}">{a.get('severity', '-')}</span></td>
                <td>{a.get('message', '-')[:80]}</td>
                <td>{a.get('created_at', '-')[:19] if a.get('created_at') else '-'}</td>
            </tr>"""

        if not alert_rows:
            alert_rows = '<tr><td colspan="4" style="text-align:center;color:#909399;">今日无告警 🎉</td></tr>'

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>{report.get('title', '日报')}</title>{ReportTemplates.BASE_CSS}</head>
<body>
<div class="container">
    <div class="header">
        <h1>{report.get('title', '日报')}</h1>
        <div class="meta">生成时间: {report.get('generated_at', '')[:19]} | 版本 v2.0</div>
    </div>

    <div class="section">
        <h2><span class="icon">📊</span> 今日概览</h2>
        <div class="cards">
            <div class="card">
                <div class="label">设备总数</div>
                <div class="value">{ov.get('total_devices', 0)}</div>
                <div class="sub">在线 {ov.get('online_devices', 0)} / 离线 {ov.get('offline_devices', 0)}</div>
            </div>
            <div class="card {cpu_class}">
                <div class="label">CPU 平均使用率</div>
                <div class="value">{cpu.get('avg', 0)}%</div>
                <div class="sub">峰值 {cpu.get('max', 0)}%</div>
            </div>
            <div class="card {mem_class}">
                <div class="label">内存平均使用率</div>
                <div class="value">{mem.get('avg', 0)}%</div>
                <div class="sub">峰值 {mem.get('max', 0)}%</div>
            </div>
            <div class="card {disk_class}">
                <div class="label">磁盘平均使用率</div>
                <div class="value">{disk.get('avg', 0)}%</div>
                <div class="sub">峰值 {disk.get('max', 0)}%</div>
            </div>
            <div class="card {alert_class}">
                <div class="label">今日告警</div>
                <div class="value">{ov.get('total_alerts', 0)}</div>
                <div class="sub">已解决 {ov.get('resolved_alerts', 0)}</div>
            </div>
        </div>
    </div>

    <div class="section">
        <h2><span class="icon">🔔</span> 告警列表</h2>
        <table><thead><tr><th>设备</th><th>级别</th><th>内容</th><th>时间</th></tr></thead>
        <tbody>{alert_rows}</tbody></table>
    </div>

    <div class="section">
        <h2><span class="icon">🖥️</span> 设备状态</h2>
        <table><thead><tr><th>设备名称</th><th>IP 地址</th><th>类型</th><th>状态</th></tr></thead>
        <tbody>{device_rows}</tbody></table>
    </div>

    {f'<div class="section"><div class="ai-box"><div class="ai-label">🤖 AI 日报总结</div><p>{ai}</p></div></div>' if ai else ''}

    <div class="footer">智能监控平台 · 自动生成 · {report.get('generated_at', '')[:19]}</div>
</div>
</body></html>"""

    # ════════════════════════════════════════════════════════
    #  周报模板 — 详细分析 + 图表
    # ════════════════════════════════════════════════════════

    @staticmethod
    def render_weekly(report: dict) -> str:
        """渲染周报 HTML"""
        ov = report.get("overview", {})
        trends = report.get("daily_trends", [])
        alerts = report.get("alert_statistics", {})
        peaks = report.get("peak_analysis", {})
        health = report.get("health_scores", [])
        ai = report.get("ai_analysis", {})

        # 趋势数据 → JS
        trend_json = ReportTemplates._to_js_array(trends, ["cpu_avg", "cpu_max", "mem_avg", "disk_avg"])
        dates_json = ReportTemplates._dates_to_js(trends)

        # 健康评分行
        health_rows = ""
        for h in health[:10]:
            grade = h.get("grade", "C")
            health_rows += f"""
            <tr>
                <td><strong>{h.get('machine_name', '-')}</strong></td>
                <td>
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span style="font-weight:700;min-width:40px;">{h.get('score', 0)}分</span>
                        <span class="tag tag-{'online' if grade in 'AB' else 'warning' if grade == 'C' else 'offline'}">{h.get('grade_label', '-')}</span>
                    </div>
                    <div class="health-bar"><div class="fill fill-{grade}" style="width:{h.get('score', 50)}%"></div></div>
                </td>
                <td>CPU {h.get('breakdown',{{}}).get('cpu',0)} | 内存 {h.get('breakdown',{{}}).get('memory',0)} | 磁盘 {h.get('breakdown',{{}}).get('disk',0)}</td>
            </tr>"""

        # 告警统计行
        alert_by_date_rows = ""
        for a in alerts.get("by_date", [])[:7]:
            alert_by_date_rows += f"""
            <tr><td>{a.get('date', '')}</td><td>{a.get('count', 0)}</td>
            <td><span class="tag tag-critical">{a.get('critical', 0)}</span></td>
            <td><span class="tag tag-warning">{a.get('warning', 0)}</span></td>
            <td><span class="tag tag-info">{a.get('info', 0)}</span></td></tr>"""

        # AI 分析
        ai_html = ""
        if ai and ai.get("summary"):
            sections_html = ""
            for s in ai.get("sections", []):
                sections_html += f"<p>{s.get('content', '')}</p>"
            ai_html = f"""
            <div class="section">
                <h2><span class="icon">🤖</span> AI 综合分析</h2>
                <div class="ai-box">{sections_html}</div>
            </div>"""

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>{report.get('title', '周报')}</title>
{ReportTemplates.BASE_CSS}
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>{report.get('title', '周报')}</h1>
        <div class="meta">周期: {ov.get('date_range', '')} | 生成时间: {report.get('generated_at', '')[:19]}</div>
    </div>

    <!-- 概览卡片 -->
    <div class="section">
        <h2><span class="icon">📊</span> 本周概览</h2>
        <div class="cards">
            <div class="card"><div class="label">管理设备</div><div class="value">{ov.get('total_devices', 0)}</div><div class="sub">在线率 {ov.get('avg_online_rate', 0)}%</div></div>
            <div class="card"><div class="label">告警总数</div><div class="value">{alerts.get('total', 0)}</div><div class="sub">本周累计告警</div></div>
            <div class="card"><div class="label">平均健康评分</div><div class="value">{ov.get('avg_health_score', 0)}</div><div class="sub">/ 100分</div></div>
        </div>
    </div>

    <!-- 7日趋势图 -->
    <div class="section">
        <h2><span class="icon">📈</span> 7日资源使用趋势</h2>
        <div id="trendChart" class="chart-container" style="height:380px;"></div>
    </div>

    <!-- 告警统计 -->
    <div class="section">
        <h2><span class="icon">🔔</span> 告警统计</h2>
        <table><thead><tr><th>日期</th><th>总计</th><th>严重</th><th>警告</th><th>信息</th></tr></thead>
        <tbody>{alert_by_date_rows}</tbody></table>
    </div>

    <!-- 峰值分析 -->
    <div class="section">
        <h2><span class="icon">⚠️</span> 峰值分析</h2>
        <div id="peakChart" class="chart-container" style="height:300px;"></div>
    </div>

    <!-- 设备健康 -->
    <div class="section">
        <h2><span class="icon">💚</span> 设备健康评分</h2>
        <table><thead><tr><th>设备</th><th>健康评分</th><th>分项得分</th></tr></thead>
        <tbody>{health_rows}</tbody></table>
    </div>

    {ai_html}

    <div class="footer">智能监控平台 · 自动生成 · {report.get('generated_at', '')[:19]}</div>
</div>

<script>
// 7日趋势图
(function() {{
    var chart = echarts.init(document.getElementById('trendChart'));
    chart.setOption({{
        tooltip: {{ trigger: 'axis' }},
        legend: {{ data: ['平均CPU%', '峰值CPU%', '平均内存%', '平均磁盘%'], bottom: 0 }},
        grid: {{ left: 50, right: 30, top: 20, bottom: 40 }},
        xAxis: {{ type: 'category', data: {dates_json} }},
        yAxis: {{ type: 'value', max: 100, axisLabel: {{ formatter: '{{value}}%' }} }},
        series: [
            {{ name: '平均CPU%', type: 'line', smooth: true, data: {trend_json[0]}, lineStyle: {{ color: '#409EFF' }}, areaStyle: {{ color: 'rgba(64,158,255,0.1)' }} }},
            {{ name: '峰值CPU%', type: 'line', smooth: true, data: {trend_json[1]}, lineStyle: {{ color: '#F56C6C', type: 'dashed' }} }},
            {{ name: '平均内存%', type: 'line', smooth: true, data: {trend_json[2]}, lineStyle: {{ color: '#67C23A' }}, areaStyle: {{ color: 'rgba(103,194,58,0.1)' }} }},
            {{ name: '平均磁盘%', type: 'line', smooth: true, data: {trend_json[3]}, lineStyle: {{ color: '#E6A23C' }} }}
        ]
    }});
}})();
</script>
</body></html>"""

    # ════════════════════════════════════════════════════════
    #  月报模板 — 综合分析 + 热力图 + 容量规划
    # ════════════════════════════════════════════════════════

    @staticmethod
    def render_monthly(report: dict) -> str:
        """渲染月报 HTML"""
        ov = report.get("overview", {})
        sla = report.get("sla_report", {})
        capacity = report.get("capacity_planning", {})
        health = report.get("health_scores", [])
        cost = report.get("cost_optimization", {})
        patterns = report.get("alert_patterns", {})
        ai = report.get("ai_analysis", {})

        # SLA 详情行
        sla_rows = ""
        for s in sla.get("details", []):
            sla_pct = s.get("sla_pct", 100)
            color = "#67C23A" if sla_pct >= 99 else ("#E6A23C" if sla_pct >= 95 else "#F56C6C")
            sla_rows += f"""
            <tr><td>{s.get('machine_name', '-')}</td>
            <td>{s.get('uptime_hours', 0)} / {s.get('total_hours', 0)} 小时</td>
            <td><span style="color:{color};font-weight:700;">{sla_pct}%</span></td></tr>"""

        # 容量规划行
        cap_rows = ""
        for p in capacity.get("devices", []):
            status_color = "#F56C6C" if p.get("needs_scale_up") else "#67C23A"
            status_text = "⚠ 需关注" if p.get("needs_scale_up") else "✓ 正常"
            cap_rows += f"""
            <tr><td><strong>{p.get('machine_name', '-')}</strong></td>
            <td>CPU {p.get('current',{{}}).get('cpu_avg',0)}% | 内存 {p.get('current',{{}}).get('mem_avg',0)}% | 磁盘 {p.get('current',{{}}).get('disk_avg',0)}%</td>
            <td style="color:{status_color};font-weight:600;">{status_text}</td>
            <td style="font-size:12px;color:#909399;">{'; '.join(p.get('recommendations', []))}</td></tr>"""

        # 成本优化行
        cost_rows = ""
        for d in cost.get("low_usage_devices", []):
            cost_rows += f"""
            <tr><td>{d.get('machine_name', '-')}</td>
            <td>CPU {d.get('cpu_avg', 0)}% / 内存 {d.get('mem_avg', 0)}%</td>
            <td style="color:#909399;">{d.get('suggestion', '-')}</td></tr>"""

        # AI 分析
        ai_html = ""
        if ai and ai.get("summary"):
            sections_html = ""
            for s in ai.get("sections", []):
                sections_html += f"<p>{s.get('content', '')}</p>"
            ai_html = f"""
            <div class="section">
                <h2><span class="icon">🤖</span> AI 深度分析</h2>
                <div class="ai-box">{sections_html}</div>
            </div>"""

        # 告警模式
        patterns_rows = ""
        for p in patterns.get("highlights", {}).get("top_patterns", []):
            patterns_rows += f"""
            <tr><td>{p.get('alert_type', '-')}</td><td>{p.get('count', 0)}</td><td>活跃 {p.get('active_days', 0)} 天</td></tr>"""

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>{report.get('title', '月报')}</title>
{ReportTemplates.BASE_CSS}
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
    .grade-dist {{ display: flex; gap: 12px; flex-wrap: wrap; }}
    .grade-item {{ flex: 1; min-width: 80px; text-align: center; padding: 12px; border-radius: 8px; }}
    .grade-A {{ background: #e6ffe6; color: #67C23A; }}
    .grade-B {{ background: #e6f0ff; color: #409EFF; }}
    .grade-C {{ background: #fff3e6; color: #E6A23C; }}
    .grade-D {{ background: #ffe6e6; color: #F56C6C; }}
    .savings {{ font-size: 28px; font-weight: 700; color: #67C23A; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>{report.get('title', '月报')}</h1>
        <div class="meta">周期: {ov.get('date_range', '')} | 生成时间: {report.get('generated_at', '')[:19]}</div>
    </div>

    <!-- 概览 -->
    <div class="section">
        <h2><span class="icon">📊</span> 月度概览</h2>
        <div class="cards">
            <div class="card"><div class="label">管理设备</div><div class="value">{ov.get('total_devices', 0)}</div><div class="sub">在线率 {ov.get('avg_online_rate', 0)}%</div></div>
            <div class="card"><div class="label">SLA 达标率</div><div class="value">{sla.get('overall', 0)}%</div><div class="sub">整体服务可用性</div></div>
            <div class="card"><div class="label">平均健康评分</div><div class="value">{ov.get('avg_health_score', 0)}</div><div class="sub">/ 100分</div></div>
            <div class="card"><div class="label">预估月节省</div><div class="savings">¥{cost.get('estimated_monthly_savings', 0):,}</div><div class="sub">低负载设备优化</div></div>
        </div>
    </div>

    <!-- 健康评分分布 -->
    <div class="section">
        <h2><span class="icon">💚</span> 设备健康评级分布</h2>
        <div id="healthDistChart" class="chart-container" style="height:300px;"></div>
    </div>

    <!-- 30日趋势 -->
    <div class="section">
        <h2><span class="icon">📈</span> 30日资源使用趋势</h2>
        <div id="monthlyTrendChart" class="chart-container" style="height:380px;"></div>
    </div>

    <!-- SLA 报告 -->
    <div class="section">
        <h2><span class="icon">🎯</span> SLA 达标报告</h2>
        <table><thead><tr><th>设备</th><th>在线时长</th><th>SLA</th></tr></thead>
        <tbody>{sla_rows}</tbody></table>
    </div>

    <!-- 容量规划 -->
    <div class="section">
        <h2><span class="icon">📐</span> 容量规划</h2>
        <table><thead><tr><th>设备</th><th>当前使用率</th><th>状态</th><th>建议</th></tr></thead>
        <tbody>{cap_rows}</tbody></table>
    </div>

    <!-- 告警模式 -->
    <div class="section">
        <h2><span class="icon">🔍</span> 告警模式分析</h2>
        <p style="margin-bottom:12px;color:#909399;">{patterns.get('alert_pattern_summary', '')}</p>
        <table><thead><tr><th>告警类型</th><th>次数</th><th>活跃天数</th></tr></thead>
        <tbody>{patterns_rows}</tbody></table>
    </div>

    <!-- 成本优化 -->
    <div class="section">
        <h2><span class="icon">💰</span> 成本优化建议</h2>
        {f'<p style="margin-bottom:12px;">发现 <strong style="color:#F56C6C;">{cost.get("total_low_usage_count", 0)}</strong> 台低负载设备，预估月度可节省约 <strong style="color:#67C23A;">¥{cost.get("estimated_monthly_savings", 0):,}</strong></p>' if cost.get("total_low_usage_count", 0) > 0 else '<p style="margin-bottom:12px;color:#67C23A;">✓ 无低负载设备，资源配置合理。</p>'}
        <table><thead><tr><th>设备</th><th>平均使用率</th><th>优化建议</th></tr></thead>
        <tbody>{cost_rows if cost_rows else '<tr><td colspan="3" style="text-align:center;color:#67C23A;">所有设备资源配置合理</td></tr>'}</tbody></table>
    </div>

    {ai_html}

    <div class="footer">智能监控平台 · 自动生成 · {report.get('generated_at', '')[:19]}</div>
</div>

<script>
// 健康评级分布
(function() {{
    var chart = echarts.init(document.getElementById('healthDistChart'));
    var grades = {{}};
    var healthData = {ReportTemplates._health_to_js(health)};
    healthData.forEach(function(h) {{
        grades[h.grade] = (grades[h.grade] || 0) + 1;
    }});
    chart.setOption({{
        tooltip: {{ trigger: 'item' }},
        series: [{{
            type: 'pie', radius: ['40%', '70%'],
            data: [
                {{ value: grades['A'] || 0, name: 'A-优秀', itemStyle: {{ color: '#67C23A' }} }},
                {{ value: grades['B'] || 0, name: 'B-良好', itemStyle: {{ color: '#409EFF' }} }},
                {{ value: grades['C'] || 0, name: 'C-一般', itemStyle: {{ color: '#E6A23C' }} }},
                {{ value: grades['D'] || 0, name: 'D-需关注', itemStyle: {{ color: '#F56C6C' }} }}
            ],
            label: {{ show: true, formatter: '{{b}}\\n{{c}} 台 ({{d}}%)' }}
        }}]
    }});
}})();

// 30日趋势 - 采样展示
(function() {{
    var chart = echarts.init(document.getElementById('monthlyTrendChart'));
    var trends = {ReportTemplates._trend_to_js(report.get('daily_trends', []))};
    chart.setOption({{
        tooltip: {{ trigger: 'axis' }},
        legend: {{ data: ['CPU均值', '内存均值', '磁盘均值'], bottom: 0 }},
        grid: {{ left: 50, right: 30, top: 20, bottom: 40 }},
        xAxis: {{ type: 'category', data: trends.dates }},
        yAxis: {{ type: 'value', max: 100, axisLabel: {{ formatter: '{{value}}%' }} }},
        series: [
            {{ name: 'CPU均值', type: 'line', smooth: true, data: trends.cpu, lineStyle: {{ color: '#409EFF' }}, areaStyle: {{ color: 'rgba(64,158,255,0.1)' }} }},
            {{ name: '内存均值', type: 'line', smooth: true, data: trends.mem, lineStyle: {{ color: '#67C23A' }}, areaStyle: {{ color: 'rgba(103,194,58,0.1)' }} }},
            {{ name: '磁盘均值', type: 'line', smooth: true, data: trends.disk, lineStyle: {{ color: '#E6A23C' }} }}
        ]
    }});
}})();
</script>
</body></html>"""

    # ────────────────────────────────────────────────────────
    #  工具方法
    # ────────────────────────────────────────────────────────

    @staticmethod
    def _to_js_array(trends: list, keys: list) -> list:
        """将趋势数据转为 JS 数组字符串的列表"""
        result = []
        for key in keys:
            values = [str(t.get(key, 0)) for t in trends]
            result.append("[" + ",".join(values) + "]")
        return result

    @staticmethod
    def _dates_to_js(trends: list) -> str:
        """提取日期列表为 JS 数组"""
        dates = [f"'{t.get('date', '')}'" for t in trends]
        return "[" + ",".join(dates) + "]"

    @staticmethod
    def _health_to_js(health: list) -> str:
        """健康数据转 JS"""
        items = []
        for h in health:
            items.append("{" + f"grade:'{h.get('grade', 'C')}',score:{h.get('score', 0)}" + "}")
        return "[" + ",".join(items) + "]"

    @staticmethod
    def _trend_to_js(trends: list) -> dict:
        """趋势数据转 JS 变量"""
        dates = [f"'{t.get('date', '')}'" for t in trends]
        cpu = [str(t.get('cpu_avg', 0)) for t in trends]
        mem = [str(t.get('mem_avg', 0)) for t in trends]
        disk = [str(t.get('disk_avg', 0)) for t in trends]
        return {
            "dates": "[" + ",".join(dates) + "]",
            "cpu": "[" + ",".join(cpu) + "]",
            "mem": "[" + ",".join(mem) + "]",
            "disk": "[" + ",".join(disk) + "]",
        }


# ── 便捷函数 ─────────────────────────────────────────────

def render_report(report: dict) -> str:
    """根据 report_type 自动选择模板渲染"""
    templates = ReportTemplates()
    report_type = report.get("report_type", "daily")

    if report_type == "daily":
        return templates.render_daily(report)
    elif report_type == "weekly":
        return templates.render_weekly(report)
    elif report_type == "monthly":
        return templates.render_monthly(report)
    else:
        return templates.render_daily(report)
