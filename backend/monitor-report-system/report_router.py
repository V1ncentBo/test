"""
报表 API 路由
═══════════
提供报表查询、手动生成、调度管理、导出下载等 REST API
"""
import io
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

logger = logging.getLogger("report_router")

router = APIRouter(prefix="/api/reports", tags=["报表管理"])


# ═══════════════════════════════════════════════════════════
#  请求/响应模型
# ═══════════════════════════════════════════════════════════

class ReportGenerateRequest(BaseModel):
    report_type: str = Field(..., description="报表类型: daily / weekly / monthly")
    period_start: Optional[str] = Field(None, description="周期起始日期 (YYYY-MM-DD)")
    period_end: Optional[str] = Field(None, description="周期结束日期 (YYYY-MM-DD)")

class ScheduleUpdateRequest(BaseModel):
    report_type: str = Field(..., description="报表类型")
    enabled: Optional[bool] = Field(None, description="启用/禁用")
    cron_expression: Optional[str] = Field(None, description="Cron 表达式")
    dingtalk_enabled: Optional[bool] = Field(None, description="钉钉推送开关")
    email_recipients: Optional[list] = Field(None, description="邮件接收人列表")

class ReportListItem(BaseModel):
    id: int
    report_type: str
    title: str
    period_start: str
    period_end: str
    status: str
    generated_at: Optional[str]

class ReportDetail(BaseModel):
    id: int
    report_type: str
    title: str
    period_start: str
    period_end: str
    html_content: Optional[str]
    json_summary: Optional[dict]
    metrics_snapshot: Optional[dict]
    alert_summary: Optional[dict]
    status: str
    generated_at: Optional[str]


# ═══════════════════════════════════════════════════════════
#  依赖注入 (从 app state 获取服务实例)
# ═══════════════════════════════════════════════════════════

def get_scheduler() -> "ReportScheduler":
    """获取调度器实例"""
    from main import app
    return app.state.report_scheduler

def get_db():
    """获取数据库 session"""
    from models.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
#  报表生成
# ═══════════════════════════════════════════════════════════

@router.post("/generate", summary="手动生成报表")
async def generate_report(
    req: ReportGenerateRequest,
    background_tasks: BackgroundTasks,
    scheduler=Depends(get_scheduler),
):
    """
    手动触发报表生成

    - **daily**: 生成今日日报
    - **weekly**: 生成本周周报
    - **monthly**: 生成本月月报
    """
    if req.report_type not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail="报表类型必须为 daily / weekly / monthly")

    # 同步执行（后台可选）
    result = scheduler.trigger_manual(req.report_type)

    if result["status"] == "failed":
        raise HTTPException(status_code=500, detail=f"生成失败: {result.get('error', '未知错误')}")

    return {
        "code": 0,
        "message": f"{req.report_type} 报表生成成功",
        "data": {
            "report_type": req.report_type,
            "file_path": result.get("file_path"),
            "generated_at": datetime.now().isoformat(),
        },
    }

# ═══════════════════════════════════════════════════════════
#  报表查询
# ═══════════════════════════════════════════════════════════

@router.get("/list", summary="获取报表列表")
async def list_reports(
    report_type: Optional[str] = Query(None, description="报表类型筛选"),
    status: Optional[str] = Query(None, description="状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    db=Depends(get_db),
):
    """获取历史报表列表，支持按类型和状态筛选"""
    sql = "SELECT * FROM report_record WHERE 1=1"
    params = {}

    if report_type:
        sql += " AND report_type = :report_type"
        params["report_type"] = report_type
    if status:
        sql += " AND status = :status"
        params["status"] = status

    # Count total
    count_sql = sql.replace("SELECT *", "SELECT COUNT(*) as cnt")
    total_result = db.execute(text(count_sql), params).mappings().fetchone()
    total = total_result["cnt"] if total_result else 0

    # Paginate
    sql += " ORDER BY generated_at DESC LIMIT :limit OFFSET :offset"
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size

    records = db.execute(text(sql), params).mappings().fetchall()

    items = []
    for r in records:
        items.append({
            "id": r["id"],
            "report_type": r["report_type"] or "daily",
            "title": r["title"] or "",
            "period_start": r["period_start"].isoformat() if hasattr(r["period_start"], 'isoformat') and r["period_start"] else str(r["period_start"]) if r["period_start"] else None,
            "period_end": r["period_end"].isoformat() if hasattr(r["period_end"], 'isoformat') and r["period_end"] else str(r["period_end"]) if r["period_end"] else None,
            "status": r["status"] or "completed",
            "generated_at": r["generated_at"].isoformat() if hasattr(r["generated_at"], 'isoformat') and r["generated_at"] else str(r["generated_at"]) if r["generated_at"] else None,
        })

    return {
        "code": 0,
        "data": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        },
    }

@router.get("/{report_id}", summary="获取报表详情")
async def get_report_detail(report_id: int, db=Depends(get_db)):
    """获取指定报表的完整内容（含 HTML 和 JSON 数据）"""
    record = db.execute(text("SELECT * FROM report_record WHERE id = :id"), {"id": report_id}).mappings().fetchone()
    if not record:
        raise HTTPException(status_code=404, detail="报表不存在")

    import json as _json
    return {
        "code": 0,
        "data": {
            "id": record["id"],
            "report_type": record["report_type"] or "daily",
            "title": record["title"] or "",
            "period_start": str(record["period_start"]) if record["period_start"] else None,
            "period_end": str(record["period_end"]) if record["period_end"] else None,
            "html_content": record["html_content"],
            "json_summary": _json.loads(record["json_summary"]) if isinstance(record["json_summary"], str) else record["json_summary"],
            "metrics_snapshot": _json.loads(record["metrics_snapshot"]) if isinstance(record["metrics_snapshot"], str) else record["metrics_snapshot"],
            "alert_summary": _json.loads(record["alert_summary"]) if isinstance(record["alert_summary"], str) else record["alert_summary"],
            "status": record["status"] or "completed",
            "generated_at": str(record["generated_at"]) if record["generated_at"] else None,
        },
    }

@router.get("/{report_id}/html", summary="查看报表 HTML", response_class=HTMLResponse)
async def view_report_html(report_id: int, db=Depends(get_db)):
    """直接在浏览器中查看报表 HTML"""
    record = db.execute(text("SELECT html_content FROM report_record WHERE id = :id"), {"id": report_id}).mappings().fetchone()
    if not record:
        raise HTTPException(status_code=404, detail="报表不存在")
    if not record["html_content"]:
        raise HTTPException(status_code=404, detail="报表 HTML 内容为空")

    return HTMLResponse(content=record["html_content"])

@router.get("/{report_id}/download", summary="下载报表 HTML 文件")
async def download_report(report_id: int, db=Depends(get_db)):
    """下载报表为 HTML 文件"""
    record = db.execute(text("SELECT * FROM report_record WHERE id = :id"), {"id": report_id}).mappings().fetchone()
    if not record:
        raise HTTPException(status_code=404, detail="报表不存在")

    if record["file_path"]:
        return FileResponse(
            path=record["file_path"],
            filename=f"{record['title'] or 'report'}.html",
            media_type="text/html",
        )

    if record["html_content"]:
        buffer = io.BytesIO(record["html_content"].encode("utf-8"))
        return StreamingResponse(
            buffer,
            media_type="text/html",
            headers={"Content-Disposition": f"attachment; filename={record['title'] or 'report'}.html"},
        )

    raise HTTPException(status_code=404, detail="无可下载的内容")

# ═══════════════════════════════════════════════════════════
#  调度管理
# ═══════════════════════════════════════════════════════════

@router.get("/schedule/list", summary="获取调度配置")
async def get_schedules(db=Depends(get_db)):
    """获取所有报表类型的调度配置"""
    schedules = db.execute(text("SELECT * FROM report_schedule")).mappings().fetchall()
    items = []
    for s in schedules:
        items.append({
            "report_type": s["report_type"],
            "enabled": bool(s["enabled"]),
            "cron_expression": s["cron_expression"],
            "delivery_methods": s["delivery_methods"],
            "ai_enabled": bool(s["ai_enabled"]),
            "retention_days": s["retention_days"],
        })
    return {"code": 0, "data": items}

@router.post("/schedule/update", summary="更新调度配置")
async def update_schedule(req: ScheduleUpdateRequest, db=Depends(get_db)):
    """更新指定报表的调度配置"""
    import json as _json

    existing = db.execute(
        text("SELECT id FROM report_schedule WHERE report_type = :rt"),
        {"rt": req.report_type}
    ).mappings().fetchone()

    if not existing:
        db.execute(
            text("INSERT INTO report_schedule (report_type, enabled, cron_expression) VALUES (:rt, 1, :cron)"),
            {"rt": req.report_type, "cron": "0 8 * * *"}
        )
        db.commit()
    else:
        updates = []
        params = {"rt": req.report_type}
        if req.enabled is not None:
            updates.append("enabled = :enabled")
            params["enabled"] = req.enabled
        if req.cron_expression:
            updates.append("cron_expression = :cron")
            params["cron"] = req.cron_expression
        if req.dingtalk_enabled is not None:
            updates.append("dingtalk_enabled = :dt")
            params["dt"] = req.dingtalk_enabled
        if req.email_recipients is not None:
            updates.append("email_recipients = :em")
            params["em"] = _json.dumps(req.email_recipients)
        if updates:
            updates.append("updated_at = NOW()")
            db.execute(text(f"UPDATE report_schedule SET {', '.join(updates)} WHERE report_type = :rt"), params)
            db.commit()

    return {
        "code": 0,
        "message": f"{req.report_type} 调度配置已更新",
    }

@router.get("/jobs/status", summary="查看定时任务状态")
async def get_jobs_status(scheduler=Depends(get_scheduler)):
    """查看当前所有调度任务的运行状态"""
    jobs = scheduler.get_jobs_status()
    return {"code": 0, "data": jobs}

# ═══════════════════════════════════════════════════════════
#  统计概览（供前端 Dashboard）
# ═══════════════════════════════════════════════════════════

@router.get("/stats/summary", summary="报表统计概览")
async def report_stats_summary(db=Depends(get_db)):
    """获取报表生成统计（供前端概览卡片）"""
    total = db.execute(text("SELECT COUNT(*) as cnt FROM report_record")).mappings().fetchone()
    stats = {
        "total_reports": total["cnt"] if total else 0,
        "last_daily": None,
        "last_weekly": None,
        "last_monthly": None,
    }

    for rtype in ["daily", "weekly", "monthly"]:
        record = db.execute(
            text("SELECT generated_at FROM report_record WHERE report_type = :rt ORDER BY generated_at DESC LIMIT 1"),
            {"rt": rtype}
        ).mappings().fetchone()
        if record and record["generated_at"]:
            stats[f"last_{rtype}"] = str(record["generated_at"])

    return {"code": 0, "data": stats}
