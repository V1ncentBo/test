"""AI 大模型分析 API 路由"""
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from models.database import get_db, MachineInfo, AIAnalysisLog, ReportRecord, AlertLog, SessionLocal
from models.schema import AIAnalysisRequest, AIAnalysisResponse
from services.ai_service import ai_service
from services.collector import metrics_service
from services.alert_service import alert_service
from datetime import datetime
from config import bj_now

from routers.auth import get_current_user, require_any_perm
router = APIRouter(prefix="/api/ai", tags=["AI智能分析"], dependencies=[Depends(require_any_perm("ai", "reports"))])


@router.post("/analyze")
def ai_analyze(req: AIAnalysisRequest, db: Session = Depends(get_db)):
    """AI 智能分析 - 故障诊断/趋势预测/优化建议"""
    results = []

    for machine_id in req.machine_ids:
        machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
        if not machine:
            continue

        # 获取最新监控数据
        metrics = metrics_service.query_latest(machine_id) or {}

        # 获取近期告警
        alerts = alert_service.get_alerts(db, machine_id=machine_id, limit=10)
        alert_list = [{
            "alert_level": a.alert_level,
            "message": a.message,
        } for a in alerts]

        machine_info = {
            "name": machine.name,
            "ip": machine.ip,
            "device_type": machine.device_type,
            "group_name": machine.group_name,
        }

        # 调用 AI
        ai_result = ai_service.analyze(
            machine_info, metrics, alert_list,
            analysis_type=req.analysis_type, query=req.query
        )

        # 保存分析记录
        log = AIAnalysisLog(
            machine_id=machine_id,
            analysis_type=req.analysis_type,
            query_text=req.query,
            diagnosis=ai_result.get("diagnosis", ""),
            prediction=ai_result.get("prediction", ""),
            optimization=ai_result.get("optimization", ""),
            raw_response=ai_result.get("raw", ""),
        )
        db.add(log)
        db.commit()
        db.refresh(log)

        results.append({
            "id": log.id,
            "machine_id": machine_id,
            "machine_name": machine.name,
            "analysis_type": req.analysis_type,
            "overview": ai_result.get("overview", ""),
            "diagnosis": ai_result.get("diagnosis", ""),
            "prediction": ai_result.get("prediction", ""),
            "optimization": ai_result.get("optimization", ""),
            "created_at": log.created_at.isoformat(),
        })

    return {"results": results}


@router.post("/chat")
def ai_chat(
    machine_id: int = Query(...),
    query: str = Query(...),
    db: Session = Depends(get_db),
):
    """AI 自然语言问答"""
    machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="设备不存在")

    metrics = metrics_service.query_latest(machine_id) or {}
    alerts = alert_service.get_alerts(db, machine_id=machine_id, limit=10)

    context = {
        "machine": {
            "name": machine.name, "ip": machine.ip,
            "device_type": machine.device_type,
        },
        "metrics": metrics,
        "alerts": [{"level": a.alert_level, "message": a.message} for a in alerts],
    }

    answer = ai_service.chat(context, query)

    # 保存对话记录
    log = AIAnalysisLog(
        machine_id=machine_id,
        analysis_type="chat",
        query_text=query,
        raw_response=answer,
    )
    db.add(log)
    db.commit()

    return {"machine_id": machine_id, "query": query, "answer": answer}


# 异步报告存储（简单内存缓存）
import uuid, threading
_report_tasks = {}  # task_id -> {"status": "generating"|"done", "report": "...", "generated_at": "..."}

@router.post("/report")
def generate_report(
    period: str = Query("daily", description="daily/weekly/monthly"),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None,
):
    """生成运维报告（异步）"""
    # 收集数据
    machines = db.query(MachineInfo).all()
    machine_data = []
    for m in machines:
        metrics = metrics_service.query_latest(m.id) or {}
        alerts_count = alert_service.get_alerts(db, machine_id=m.id, limit=100)
        machine_data.append({
            "name": m.name, "ip": m.ip, "type": m.device_type,
            "status": m.online_status,
            "cpu": metrics.get("cpu_percent", 0),
            "memory": metrics.get("memory_percent", 0),
            "disk": metrics.get("disk_percent", 0),
            "alert_count": len(alerts_count),
        })

    task_id = str(uuid.uuid4())[:8]
    _report_tasks[task_id] = {"status": "generating", "report": ""}

    def _generate():
        try:
            report = ai_service.generate_report(machine_data, period)
            generated_at = bj_now().isoformat()
            _report_tasks[task_id] = {
                "status": "done",
                "report": report,
                "generated_at": generated_at
            }
            # 自动存档到数据库
            try:
                save_db = SessionLocal()
                rec = ReportRecord(
                    period=period, content=report,
                    machine_count=len(machines),
                    alert_count=sum(m["alert_count"] for m in machine_data)
                )
                save_db.add(rec)
                save_db.commit()
                save_db.close()
            except Exception:
                pass
        except Exception as e:
            _report_tasks[task_id] = {
                "status": "error",
                "error": str(e)
            }

    threading.Thread(target=_generate, daemon=True).start()
    return {"task_id": task_id, "status": "generating"}


@router.get("/report/list")
def list_reports(db: Session = Depends(get_db), limit: int = Query(20)):
    """获取已存档的报告列表"""
    reports = db.query(ReportRecord) \
        .order_by(ReportRecord.created_at.desc()) \
        .limit(limit).all()
    return [{
        "id": r.id,
        "period": r.period,
        "machine_count": r.machine_count,
        "alert_count": r.alert_count,
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "summary": r.content[:120].replace("\n", " ").replace("**", "").replace("#", "").strip()
    } for r in reports]


@router.get("/report/saved/{report_id}")
def get_saved_report(report_id: int, db: Session = Depends(get_db)):
    """获取单条已存档报告"""
    r = db.query(ReportRecord).filter(ReportRecord.id == report_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {
        "id": r.id, "period": r.period, "content": r.content,
        "machine_count": r.machine_count, "alert_count": r.alert_count,
        "created_at": r.created_at.isoformat() if r.created_at else ""
    }


@router.delete("/report/saved/{report_id}")
def delete_saved_report(report_id: int, db: Session = Depends(get_db)):
    """删除一条已存档报告"""
    r = db.query(ReportRecord).filter(ReportRecord.id == report_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="报告不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.get("/report/status/{task_id}")
def get_report_status(task_id: str):
    """查询报告生成状态"""
    task = _report_tasks.get(task_id)
    if not task:
        return {"status": "not_found"}
    return task


@router.get("/history/{machine_id}")
def get_analysis_history(
    machine_id: int,
    limit: int = Query(20),
    db: Session = Depends(get_db),
):
    """获取设备AI分析历史记录"""
    logs = db.query(AIAnalysisLog).filter(
        AIAnalysisLog.machine_id == machine_id
    ).order_by(AIAnalysisLog.created_at.desc()).limit(limit).all()

    return [{
        "id": log.id,
        "analysis_type": log.analysis_type,
        "query_text": log.query_text,
        "diagnosis": log.diagnosis,
        "prediction": log.prediction,
        "optimization": log.optimization,
        "raw_response": log.raw_response[:500] if log.raw_response else "",
        "created_at": log.created_at.isoformat(),
    } for log in logs]

# ---------- 告警AI根因分析 ----------

@router.post("/alert/{alert_id}")
def analyze_alert(alert_id: int, db: Session = Depends(get_db)):
    """AI分析告警根因：收集告警时间点周围的指标数据+进程快照，分析可能原因"""
    alert = db.query(AlertLog).filter(AlertLog.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="告警记录不存在")

    machine = db.query(MachineInfo).filter(MachineInfo.id == alert.machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="设备不存在")

    # 收集告警时间 ±10分钟的指标数据
    from datetime import timedelta
    alert_time = alert.created_at
    start = (alert_time - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (alert_time + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 获取告警前后指标
    period_metrics = {}
    try:
        raw = metrics_service.query_range(alert.machine_id, start, end)
        # 提取关键指标并计算平均值/最大值
        key_fields = {"cpu_percent", "memory_percent", "disk_percent",
                      "disk_read_mbps", "disk_write_mbps", "disk_read_iops",
                      "disk_write_iops", "network_in_mbps", "network_out_mbps",
                      "iowait", "tcp_established", "tcp_timewait",
                      "load_1m", "load_5m", "oom_events", "net_drops_total",
                      "swap_in_rate", "swap_out_rate", "mem_page_faults",
                      "context_switches", "d_state_procs", "zombie_count",
                      "cpu_steal", "kernel_errors"}
        for field in key_fields:
            vals = [p["value"] for p in raw.get(field, []) if p.get("value") is not None]
            if vals:
                period_metrics[field] = {
                    "avg": round(sum(vals)/len(vals), 2),
                    "max": round(max(vals), 2),
                    "min": round(min(vals), 2),
                    "latest": round(vals[-1], 2),
                }
    except Exception as e:
        period_metrics["_error"] = str(e)

    # 获取告警时刻的TOP进程
    top_procs = {}
    try:
        proc_data = raw.get("top_processes", [])
        if proc_data:
            top_procs = proc_data[-1]["value"] if proc_data else "未知"
    except Exception:
        top_procs = "未知"

    # 构建AI分析prompt
    import json
    context = {
        "machine": {"name": machine.name, "ip": machine.ip, "type": machine.device_type},
        "alert": {
            "type": alert.alert_type,
            "level": alert.alert_level,
            "metric": alert.metric_name,
            "value": alert.current_value,
            "threshold": alert.threshold_value,
            "time": alert.created_at.isoformat(),
        },
        "period_metrics": period_metrics,
        "top_processes": str(top_procs)[:2000],
    }

    prompt = f"""请分析以下告警的可能原因。

设备信息：{json.dumps(context['machine'], ensure_ascii=False)}
告警信息：{json.dumps(context['alert'], ensure_ascii=False)}
告警前后15分钟的指标数据（avg/max/min/latest）：{json.dumps(context['period_metrics'], ensure_ascii=False, indent=2)}
告警时的TOP进程：{context['top_processes']}

请按以下格式回答：
1. 【根因分析】判断最可能的原因（结合进程、指标、负载等综合分析）
2. 【关联指标】列出与告警相关的异常指标及其关联性
3. 【处理建议】给出具体的排查步骤和处置方案
请控制在300字以内，直接给结论，不要铺垫。"""

    try:
        analysis = ai_service.chat(context, prompt)
        alert.ai_analysis = analysis
        db.commit()
        return {"success": True, "analysis": analysis}
    except Exception as e:
        return {"success": False, "error": str(e)}
