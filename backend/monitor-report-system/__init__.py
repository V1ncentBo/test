"""
监控平台报表系统 v2.0
═══════════════════════
提供日报/周报/月报三层级报表自动生成与分发。

模块:
- report_models: 数据模型（ReportRecord / ReportSchedule / ReportTemplate）
- report_service: 核心报表生成器（数据采集、聚合、AI 分析）
- report_templates: HTML 模板渲染器
- scheduler_service: 定时调度器
- report_router: REST API 路由
"""
__version__ = "2.0.0"
