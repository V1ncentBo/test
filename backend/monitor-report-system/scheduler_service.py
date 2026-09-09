"""
定时调度服务
═══════════
基于 APScheduler 实现报表自动生成与分发
- 日报: 每天 8:00 自动生成
- 周报: 每周一 9:00 自动生成
- 月报: 每月 1 号 10:00 自动生成
"""
import logging
import os
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from report_service import ReportGenerator
from report_templates import render_report

logger = logging.getLogger("report_scheduler")


class ReportScheduler:
    """
    报表调度器
    管理日报/周报/月报的自动生成任务
    """

    def __init__(
        self,
        db_session_factory,
        influx_client,
        ai_client=None,
        db_url: str = None,
        output_dir: str = "/opt/monitor-platform/reports",
        config: dict = None,
    ):
        """
        初始化调度器

        Args:
            db_session_factory: SQLAlchemy session factory
            influx_client: InfluxDB client
            ai_client: AI 客户端（可选）
            db_url: APScheduler 持久化数据库 URL
            output_dir: 报表输出目录
            config: 调度配置（可覆盖默认 cron）
        """
        self.db_factory = db_session_factory
        self.influx = influx_client
        self.ai = ai_client
        self.output_dir = output_dir
        self.config = config or {}

        # 默认调度配置
        self.default_config = {
            "daily": {"enabled": True, "cron": "0 8 * * *", "hour": 8, "minute": 0},
            "weekly": {"enabled": True, "cron": "0 9 * * 1", "day_of_week": 0, "hour": 9, "minute": 0},
            "monthly": {"enabled": True, "cron": "0 10 1 * *", "day": 1, "hour": 10, "minute": 0},
        }

        # 合并用户配置
        for report_type in ["daily", "weekly", "monthly"]:
            if report_type in self.config:
                self.default_config[report_type].update(self.config[report_type])

        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)

        # 初始化调度器
        jobstores = {}
        if db_url:
            jobstores["default"] = SQLAlchemyJobStore(url=db_url)

        self.scheduler = BackgroundScheduler(
            jobstores=jobstores,
            timezone="Asia/Shanghai",
            job_defaults={
                "coalesce": True,         # 合并错过的任务
                "max_instances": 1,       # 同一任务最多 1 个实例
                "misfire_grace_time": 300,  # 错过 5 分钟内仍然执行
            },
        )

    def start(self):
        """启动调度器"""
        self._register_jobs()
        self.scheduler.start()
        logger.info("Report scheduler started with %d jobs", len(self.scheduler.get_jobs()))

    def stop(self):
        """停止调度器"""
        self.scheduler.shutdown(wait=False)
        logger.info("Report scheduler stopped")

    def _register_jobs(self):
        """注册所有定时任务"""
        # 日报
        if self.default_config["daily"]["enabled"]:
            self.scheduler.add_job(
                self.generate_daily_report,
                trigger=CronTrigger(
                    hour=self.default_config["daily"]["hour"],
                    minute=self.default_config["daily"]["minute"],
                    timezone="Asia/Shanghai",
                ),
                id="report_daily",
                name="日报自动生成",
                replace_existing=True,
            )
            logger.info("Daily report job registered: %s", self.default_config["daily"]["cron"])

        # 周报
        if self.default_config["weekly"]["enabled"]:
            self.scheduler.add_job(
                self.generate_weekly_report,
                trigger=CronTrigger(
                    day_of_week=self.default_config["weekly"]["day_of_week"],
                    hour=self.default_config["weekly"]["hour"],
                    minute=self.default_config["weekly"]["minute"],
                    timezone="Asia/Shanghai",
                ),
                id="report_weekly",
                name="周报自动生成",
                replace_existing=True,
            )
            logger.info("Weekly report job registered: %s", self.default_config["weekly"]["cron"])

        # 月报
        if self.default_config["monthly"]["enabled"]:
            self.scheduler.add_job(
                self.generate_monthly_report,
                trigger=CronTrigger(
                    day=self.default_config["monthly"]["day"],
                    hour=self.default_config["monthly"]["hour"],
                    minute=self.default_config["monthly"]["minute"],
                    timezone="Asia/Shanghai",
                ),
                id="report_monthly",
                name="月报自动生成",
                replace_existing=True,
            )
            logger.info("Monthly report job registered: %s", self.default_config["monthly"]["cron"])

    # ── 定时任务回调 ──────────────────────────────────────

    def generate_daily_report(self):
        """日报定时回调"""
        return self._generate_and_save("daily")

    def generate_weekly_report(self):
        """周报定时回调"""
        return self._generate_and_save("weekly")

    def generate_monthly_report(self):
        """月报定时回调"""
        return self._generate_and_save("monthly")

    # ── 核心生成逻辑 ──────────────────────────────────────

    def _generate_and_save(self, report_type: str) -> dict:
        """
        生成报表并保存到数据库 + 文件系统

        Returns:
            dict: {"status": "success|failed", "report": {...}, "file_path": "..."}
        """
        session = self.db_factory()
        try:
            logger.info("Starting %s report generation...", report_type)

            # 1. 生成报表数据
            generator = ReportGenerator(session, self.influx, self.ai)
            report = generator.generate(report_type)

            # 2. 渲染 HTML
            html = render_report(report)

            # 3. 保存 HTML 文件
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{report_type}_{timestamp}.html"
            file_path = os.path.join(self.output_dir, filename)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html)

            # 4. 保存到数据库
            self._save_to_db(session, report_type, report, html, file_path)

            # 5. 分发（钉钉、邮件等）
            self._dispatch_report(session, report_type, report, html)

            session.commit()
            logger.info("%s report generated successfully → %s", report_type, file_path)

            return {"status": "success", "report": report, "file_path": file_path}

        except Exception as e:
            session.rollback()
            logger.error("Failed to generate %s report: %s", report_type, str(e), exc_info=True)
            return {"status": "failed", "error": str(e)}

        finally:
            session.close()

    def _save_to_db(self, session, report_type: str, report: dict, html: str, file_path: str):
        """保存报表记录到 MySQL"""
        try:
            from report_models import ReportRecord, ReportStatus

            record = ReportRecord()
            record.report_type = report_type
            record.title = report.get("title", "")
            record.period_start = report.get("period_start")
            record.period_end = report.get("period_end")
            record.html_content = html
            record.json_summary = report
            record.status = ReportStatus.COMPLETED
            record.generated_at = datetime.now()
            record.file_path = file_path
            record.metrics_snapshot = report.get("metrics_snapshot")
            record.alert_summary = report.get("alert_statistics") or report.get("alerts")

            # 周报/月报特有
            if report_type in ("weekly", "monthly"):
                record.trend_data = report.get("daily_trends")
                record.health_scores = report.get("health_scores")

            if report_type == "monthly":
                record.capacity_planning = report.get("capacity_planning")
                record.anomaly_patterns = report.get("alert_patterns")

            session.add(record)
            session.flush()
            logger.debug("Report record saved to DB, id=%d", record.id)

        except Exception as e:
            logger.error("Failed to save report to DB: %s", e, exc_info=True)
            raise

    def _dispatch_report(self, session, report_type: str, report: dict, html: str):
        """分发报表（钉钉/邮件）"""
        try:
            from report_models import ReportSchedule

            schedule = session.query(ReportSchedule).filter(
                ReportSchedule.report_type == report_type,
                ReportSchedule.enabled == True,
            ).first()

            if not schedule:
                logger.debug("No schedule config for %s, skipping dispatch", report_type)
                return

            # 钉钉推送
            if schedule.dingtalk_enabled:
                self._send_dingtalk(report_type, report)

            # 邮件推送
            if schedule.email_recipients:
                self._send_email(schedule.email_recipients, report_type, report, html)

        except Exception as e:
            logger.error("Failed to dispatch report: %s", e)

    def _send_dingtalk(self, report_type: str, report: dict):
        """通过钉钉机器人推送报表摘要"""
        try:
            from services.dingtalk import send_markdown

            ov = report.get("overview", {})
            title_map = {
                "daily": f"📋 日报 - {ov.get('date', '')}",
                "weekly": f"📊 周报 - {ov.get('date_range', '')}",
                "monthly": f"📈 月报 - {ov.get('month', '')}",
            }

            # 构建 Markdown 摘要
            markdown = f"""## {title_map.get(report_type, '报表')}

> 设备: {ov.get('total_devices', 0)} 台 | 在线率: {ov.get('avg_online_rate', 0)}%

"""
            if report_type == "daily":
                ms = report.get("metrics_snapshot", {})
                markdown += f"""
- CPU: {ms.get('cpu',{}).get('avg',0)}% (峰值 {ms.get('cpu',{}).get('max',0)}%)
- 内存: {ms.get('memory',{}).get('avg',0)}% (峰值 {ms.get('memory',{}).get('max',0)}%)
- 告警: {ov.get('total_alerts',0)} 个"""

            elif report_type == "weekly":
                health = report.get("health_scores", [])
                top_alert_devices = report.get("alert_statistics", {}).get("top_devices", [])
                markdown += f"""
- 健康评分均值: **{ov.get('avg_health_score', 0)}/100**
- 告警总数: {report.get('alert_statistics',{}).get('total',0)} 个"""

                if top_alert_devices:
                    markdown += f"\n- 告警最多的设备: {top_alert_devices[0].get('name','')}"

            elif report_type == "monthly":
                markdown += f"""
- **SLA 达标率: {report.get('sla_report',{}).get('overall',0)}%**
- 健康评分均值: **{ov.get('avg_health_score', 0)}/100**
- 需扩容设备: {len(report.get('capacity_planning',{}).get('devices_need_attention',[]))} 台"""

            markdown += f"\n\n> 完整报表请在平台内查看 | {datetime.now().strftime('%Y-%m-%d %H:%M')}"

            send_markdown(markdown, title=title_map.get(report_type, "监控报表"))
            logger.info("Report dispatched to DingTalk: %s", report_type)

        except ImportError:
            logger.warning("DingTalk module not available, skipping")
        except Exception as e:
            logger.error("Failed to send DingTalk notification: %s", e)

    def _send_email(self, recipients: list, report_type: str, report: dict, html: str):
        """通过邮件发送报表"""
        # 预留接口，后续对接 SMTP
        logger.info("Email dispatch for %s report: recipients=%s (skipped - not configured)",
                     report_type, len(recipients))

    # ── 手动触发 ──────────────────────────────────────────

    def trigger_manual(self, report_type: str) -> dict:
        """手动触发报表生成（API 调用）"""
        logger.info("Manual trigger: %s report", report_type)
        return self._generate_and_save(report_type)

    # ── 状态查询 ──────────────────────────────────────────

    def get_jobs_status(self) -> list:
        """获取所有调度任务状态"""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            })
        return jobs
