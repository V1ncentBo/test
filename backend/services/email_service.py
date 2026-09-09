"""邮件告警通知服务"""
import os, json, time, smtplib
from email.mime.text import MIMEText
from datetime import datetime
from config import PLATFORM_PUBLIC_URL

SETTINGS_FILE = "/app/data/notification_settings.json"
COOLDOWN_FILE = "/app/data/email_cooldown.json"

def _load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {}

def _load_cooldown():
    if os.path.exists(COOLDOWN_FILE):
        with open(COOLDOWN_FILE, "r") as f:
            return json.load(f)
    return {}

def _save_cooldown(cache):
    os.makedirs(os.path.dirname(COOLDOWN_FILE), exist_ok=True)
    with open(COOLDOWN_FILE, "w") as f:
        json.dump(cache, f)

_cooldown = _load_cooldown()

def send_alert(machine_name: str, machine_ip: str, machine_id: int,
               alert_type: str, alert_level: str, metric_name: str,
               current_value: float, threshold_value: float) -> bool:
    """发送邮件告警，有冷却期（同设备同类型30分钟内不重复发送）"""
    try:
        settings = _load_settings()
        cfg = settings.get("email", {})
        if not cfg or not cfg.get("host") or not cfg.get("user"):
            return False

        # 冷却检查
        key = f"{machine_id}:{alert_type}"
        now = time.time()
        if _cooldown.get(key, 0) and now - _cooldown[key] < 1800:
            return False
        _cooldown[key] = now
        _save_cooldown(_cooldown)

        # 构建邮件
        level_text = "严重" if alert_level == "critical" else "警告"
        subject = f"[监控告警-{level_text}] {machine_name} {metric_name} 异常"

        platform_url = PLATFORM_PUBLIC_URL or "http://localhost"

        body = f"""设备名称: {machine_name}
IP 地址: {machine_ip}
告警类型: {metric_name}
告警级别: {level_text}
当前值: {current_value}%
阈值: {threshold_value}%
时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

详情请登录监控平台查看: {platform_url}
"""
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = cfg["user"]
        msg["To"] = cfg.get("to", cfg["user"])

        if cfg.get("port") == 465:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=10)
            server.starttls()
        server.login(cfg["user"], cfg["password"])
        recipients = [a.strip() for a in cfg.get("to", cfg["user"]).split(",") if a.strip()]
        server.sendmail(cfg["user"], recipients, msg.as_string())
        server.quit()
        print(f"[Email] Alert sent: {machine_name} {metric_name}")
        return True
    except Exception as e:
        print(f"[Email] Send failed: {e}")
        return False
