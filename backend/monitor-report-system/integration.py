"""
监控平台 main.py 集成代码片段
═══════════════════════════════════════════
将报表系统集成到现有的 FastAPI 应用中。
以下代码需要添加到 /opt/monitor-platform/backend/main.py
"""
# ============================================================
#  在 main.py 顶部添加 import
# ============================================================
# from report_router import router as report_router
# from scheduler_service import ReportScheduler
# from apscheduler.schedulers.background import BackgroundScheduler


# ============================================================
#  在 create_app() 或 app 初始化处添加
# ============================================================
"""
# 1. 注册报表路由
app.include_router(report_router)

# 2. 初始化报表调度器
@app.on_event("startup")
async def start_report_scheduler():
    from services.influx_service import influx_client  # 复用现有的 InfluxDB 客户端
    from services.ai_service import ai_client           # 复用现有的 AI 客户端

    scheduler = ReportScheduler(
        db_session_factory=SessionLocal,
        influx_client=influx_client,
        ai_client=ai_client,
        db_url="sqlite:///jobs.sqlite" if not DATABASE_URL else None,
        output_dir="/opt/monitor-platform/reports",
        config={
            "daily": {"hour": 8, "minute": 0},
            "weekly": {"day_of_week": 0, "hour": 9, "minute": 0},
            "monthly": {"day": 1, "hour": 10, "minute": 0},
        },
    )
    scheduler.start()
    app.state.report_scheduler = scheduler


@app.on_event("shutdown")
async def stop_report_scheduler():
    if hasattr(app.state, "report_scheduler"):
        app.state.report_scheduler.stop()
"""


# ============================================================
#  依赖注入：在所有路由文件中添加
# ============================================================
"""
# 在 report_router.py 中，以下依赖会自动从 app.state 获取:
def get_scheduler():
    from main import app
    return app.state.report_scheduler

def get_db():
    from main import get_db as _get_db
    return next(_get_db())
"""


print("""
╔══════════════════════════════════════════════════════════╗
║        报表系统 v2.0 集成说明                             ║
╠══════════════════════════════════════════════════════════╣
║  1. 复制 monitor-report-system/ 到服务器:                ║
║     rsync -avz monitor-report-system/ root@<平台服务器IP>:/opt/monitor-platform/backend/
║                                                          ║
║  2. 执行数据库迁移:                                      ║
║     mysql -u root -p monitor_platform < migration.sql    ║
║                                                          ║
║  3. 安装依赖:                                            ║
║     pip install apscheduler                              ║
║                                                          ║
║  4. 在 main.py 中按照本文件注释集成                       ║
║                                                          ║
║  5. 重启后端:                                            ║
║     docker-compose restart monitor-backend               ║
║                                                          ║
║  6. 验证:                                                ║
║     curl http://localhost:8000/api/reports/list          ║
║     curl -X POST http://localhost:8000/api/reports/generate   ║
║       -H "Content-Type: application/json"                ║
║       -d '{"report_type":"daily"}'                       ║
╚══════════════════════════════════════════════════════════╝
""")
