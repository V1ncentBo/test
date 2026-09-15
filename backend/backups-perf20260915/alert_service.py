"""告警服务"""
import os
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from models.database import AlertLog, MachineInfo
from config import DEFAULT_ALERT_THRESHOLDS

# 端口级告警阈值（可后续下沉到设备维度配置）
PORT_UTIL_THRESHOLD = float(os.environ.get("PORT_UTIL_THRESHOLD", "85"))
PORT_ERROR_DELTA = 50  # 单轮端口错包/丢包增量阈值
PORT_ALERT_COOLDOWN = timedelta(minutes=30)

# 维护每个设备上次采集的端口错包累计值，用于增量判断
_port_prev_errors: dict = {}


class AlertService:
    """告警检测与记录服务"""

    def __init__(self):
        self._prev_values = {}    # machine_id -> {metric_key: last_value}

    def check_and_alert(self, db: Session, machine_id: int, metrics: dict) -> list:
        """检查单设备指标，返回触发的告警列表"""
        alerts = []

        machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
        if not machine or not machine.monitor_enabled:
            return alerts

        thresholds = {
            "cpu_percent": machine.cpu_threshold or DEFAULT_ALERT_THRESHOLDS["cpu_percent"],
            "memory_percent": machine.memory_threshold or DEFAULT_ALERT_THRESHOLDS["memory_percent"],
            "disk_percent": machine.disk_threshold or DEFAULT_ALERT_THRESHOLDS["disk_percent"],
            "zombie_count": 10,
            "oom_events": 0,
            "d_state_procs": 20,
            "iowait": 50,
            "net_errors_total": 500,
            "net_drops_total": 500,
            "tcp_closewait": 500,
        }

        # 累计型指标：只对增量告警
        cumulative = {"oom_events", "net_errors_total", "net_drops_total"}
        mid_key = str(machine_id)
        if mid_key not in self._prev_values:
            self._prev_values[mid_key] = {}
        prev = self._prev_values[mid_key]

        checks = [
            ("cpu_percent", "CPU使用率", metrics.get("cpu_percent", 0), "%"),
            ("memory_percent", "内存使用率", metrics.get("memory_percent", 0), "%"),
            ("disk_percent", "磁盘使用率", metrics.get("disk_percent", 0), "%"),
            ("zombie_count", "僵尸进程数", metrics.get("zombie_count", 0), "个"),
            ("oom_events", "OOM事件数", metrics.get("oom_events", 0), "个"),
            ("d_state_procs", "D状态进程数", metrics.get("d_state_procs", 0), "个"),
            ("iowait", "IO等待", metrics.get("iowait", 0), "%"),
            ("net_errors_total", "网卡错误数", metrics.get("net_errors_total", 0), "个"),
            ("net_drops_total", "网卡丢包数", metrics.get("net_drops_total", 0), "个"),
            ("tcp_closewait", "TCP CLOSE_WAIT", metrics.get("tcp_closewait", 0), "个"),
        ]

        for metric_key, metric_name, value, unit in checks:
            threshold = thresholds.get(metric_key, 9999)
            display_value = value

            # 累计型指标：计算增量
            if metric_key in cumulative:
                prev_val = prev.get(metric_key, 0)
                if prev_val > 0 and value > prev_val:
                    display_value = value - prev_val
                    triggered = display_value > threshold
                else:
                    triggered = False
                prev[metric_key] = value
            else:
                triggered = value >= threshold

            if not triggered:
                continue

            # 冷却期检查（同指标30分钟不重复）
            recent = db.query(AlertLog).filter(
                AlertLog.machine_id == machine_id,
                AlertLog.alert_type == metric_key,
                AlertLog.status == "unresolved",
                AlertLog.created_at >= datetime.now() - timedelta(minutes=30),
            ).first()
            if recent:
                continue

            level = "critical" if (unit == "%" and value >= 95) else "warning"
            message = f"[{machine.name}] {metric_name} 达到 {display_value}{unit}，超过阈值 {threshold}{unit}"

            alert = AlertLog(
                machine_id=machine_id,
                alert_type=metric_key,
                alert_level=level,
                metric_name=metric_name,
                current_value=display_value,
                threshold_value=threshold,
                message=message,
                status="unresolved",
            )
            db.add(alert)
            alerts.append(alert)

        if alerts:
            db.commit()
        return alerts

    def check_port_alerts(self, db: Session, machine_id: int, ports: list, machine=None) -> list:
        """端口级告警：端口 DOWN / 利用率超阈值 / 错包增量超阈值。
        ports: [{index, name, speed, oper_status, util_in, util_out,
                 in_errors, out_errors, in_discards, out_discards}]
        """
        alerts = []
        if machine is None:
            machine = db.query(MachineInfo).filter(MachineInfo.id == machine_id).first()
        if not machine:
            return alerts
        prefix = f"[{machine.name}]"
        prev_errors = _port_prev_errors.setdefault(machine_id, {})
        seen = set()
        for p in ports:
            pidx = p.get("index")
            pname = p.get("name") or f"ifIndex{pidx}"
            seen.add(pidx)
            speed = int(p.get("speed") or 0)
            oper = int(p.get("oper_status") or 0)
            is_physical = speed > 0

            # 1) 端口 DOWN（仅物理端口，避免 VLAN/逻辑口噪音）
            if oper != 1 and is_physical:
                atype = f"port_down_{pidx}"
                if not self._in_cooldown(db, machine_id, atype):
                    msg = f"{prefix} 端口 {pname} 状态变为 DOWN"
                    alerts.append(self._make_alert(db, machine_id, atype, "warning",
                                                   f"端口{pname}状态", oper, 1, msg))

            # 2) 端口利用率超阈值
            util_in = float(p.get("util_in") or 0)
            util_out = float(p.get("util_out") or 0)
            util = max(util_in, util_out)
            if util >= PORT_UTIL_THRESHOLD:
                atype = f"port_util_{pidx}"
                if not self._in_cooldown(db, machine_id, atype):
                    level = "critical" if util >= 95 else "warning"
                    msg = (f"{prefix} 端口 {pname} 利用率达 {util:.1f}%（入 {util_in:.1f}% / 出 {util_out:.1f}%），"
                           f"超过阈值 {PORT_UTIL_THRESHOLD:.0f}%")
                    alerts.append(self._make_alert(db, machine_id, atype, level,
                                                   f"端口{pname}利用率", round(util, 1), PORT_UTIL_THRESHOLD, msg))

            # 3) 端口错包/丢包增量超阈值
            total_err = (int(p.get("in_errors") or 0) + int(p.get("out_errors") or 0)
                         + int(p.get("in_discards") or 0) + int(p.get("out_discards") or 0))
            prev_total = prev_errors.get(pidx, 0)
            delta = total_err - prev_total if prev_total > 0 else 0
            if prev_total > 0 and delta >= PORT_ERROR_DELTA:
                atype = f"port_err_{pidx}"
                if not self._in_cooldown(db, machine_id, atype):
                    msg = f"{prefix} 端口 {pname} 本轮新增错包/丢包 {delta}（累计 {total_err}）"
                    alerts.append(self._make_alert(db, machine_id, atype, "warning",
                                                   f"端口{pname}错包", delta, PORT_ERROR_DELTA, msg))
            prev_errors[pidx] = total_err

        # 清理已不存在端口的历史
        for k in list(prev_errors.keys()):
            if k not in seen:
                prev_errors.pop(k, None)
        if alerts:
            db.commit()
        return alerts

    def _in_cooldown(self, db: Session, machine_id: int, alert_type: str) -> bool:
        """同类型告警 30 分钟内不重复触发"""
        recent = db.query(AlertLog).filter(
            AlertLog.machine_id == machine_id,
            AlertLog.alert_type == alert_type,
            AlertLog.status == "unresolved",
            AlertLog.created_at >= datetime.now() - PORT_ALERT_COOLDOWN,
        ).first()
        return recent is not None

    def _make_alert(self, db, machine_id, alert_type, level, metric_name, current, threshold, message):
        alert = AlertLog(
            machine_id=machine_id, alert_type=alert_type, alert_level=level,
            metric_name=metric_name, current_value=float(current),
            threshold_value=float(threshold), message=message, status="unresolved",
        )
        db.add(alert)
        return alert

    def resolve_alert(self, db: Session, alert_id: int):
        alert = db.query(AlertLog).filter(AlertLog.id == alert_id).first()
        if alert:
            alert.status = "resolved"
            alert.resolved_at = datetime.now()
            db.commit()

    def get_alerts(self, db: Session, machine_id: int = None, level: str = None,
                   status: str = None, limit: int = 100, offset: int = 0):
        q = db.query(AlertLog)
        if machine_id:
            q = q.filter(AlertLog.machine_id == machine_id)
        if level:
            q = q.filter(AlertLog.alert_level == level)
        if status:
            q = q.filter(AlertLog.status == status)
        return q.order_by(AlertLog.created_at.desc()).offset(offset).limit(limit).all()

    def get_today_alert_count(self, db: Session) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        return db.query(AlertLog).filter(
            AlertLog.created_at >= today
        ).count()


alert_service = AlertService()


def notify_alerts(alerts, machine, ws_manager):
    """统一推送：WebSocket 实时广播 + 钉钉 + 邮件。供各采集器与 push 接口复用，避免重复代码。"""
    if not alerts:
        return
    # 局部导入，避免顶层循环依赖
    from services.dingtalk import send_alert as dingtalk_send
    from services.email_service import send_alert as email_send
    for alert in alerts:
        try:
            ws_manager.broadcast_alert({
                "id": alert.id,
                "machine_id": alert.machine_id,
                "alert_type": alert.alert_type,
                "alert_level": alert.alert_level,
                "message": alert.message,
                "created_at": alert.created_at.isoformat(),
            })
        except Exception:
            pass
        try:
            if machine:
                dingtalk_send(
                    machine_name=machine.name, machine_ip=machine.ip, machine_id=machine.id,
                    alert_type=alert.alert_type, alert_level=alert.alert_level,
                    metric_name=alert.metric_name, current_value=alert.current_value,
                    threshold_value=alert.threshold_value,
                )
        except Exception:
            pass
        try:
            if machine:
                email_send(
                    machine_name=machine.name, machine_ip=machine.ip, machine_id=machine.id,
                    alert_type=alert.alert_type, alert_level=alert.alert_level,
                    metric_name=alert.metric_name, current_value=alert.current_value,
                    threshold_value=alert.threshold_value,
                )
        except Exception:
            pass
