"""告警 API 路由"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from models.database import get_db
from models.schema import AlertResponse
from services.alert_service import alert_service

from routers.auth import require_any_perm
router = APIRouter(prefix="/api/alerts", tags=["告警管理"], dependencies=[Depends(require_any_perm("alerts", "dashboard"))])


@router.get("/", response_model=List[AlertResponse])
def list_alerts(
    machine_id: Optional[int] = Query(None),
    level: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    return alert_service.get_alerts(db, machine_id, level, status, limit, offset)


@router.put("/{alert_id}/resolve")
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    alert_service.resolve_alert(db, alert_id)
    return {"message": "已标记为已解决"}


@router.post("/batch-resolve")
def batch_resolve(body: dict, db: Session = Depends(get_db)):
    """批量标记已处理"""
    ids = body.get("ids", [])
    if not ids:
        return {"message": "未选择任何告警"}
    resolved = 0
    for aid in ids:
        try:
            alert_service.resolve_alert(db, int(aid))
            resolved += 1
        except Exception:
            pass
    return {"message": f"已处理 {resolved} 条告警"}
