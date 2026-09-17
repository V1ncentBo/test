"""通知设置 API"""
import json, os
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, List

from routers.auth import get_current_user, require_any_perm, require_admin

from services.crypto import encrypt_password, decrypt_password

SETTINGS_FILE = "/app/data/notification_settings.json"

router = APIRouter(prefix="/api/settings", tags=["通知设置"], dependencies=[Depends(get_current_user)])


class DingtalkHook(BaseModel):
    name: str = ""
    url: str = ""
    enabled: bool = True


class EmailSettings(BaseModel):
    host: str = ""
    port: int = 465
    user: str = ""
    password: str = ""
    to: str = ""


class NotificationSettings(BaseModel):
    dingtalk: Optional[List[DingtalkHook]] = None
    email: Optional[EmailSettings] = None


def _load() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        # 解密 SMTP 密码（兼容已加密和历史明文）
        email = data.get("email")
        if email and email.get("password"):
            email["password"] = decrypt_password(email["password"])
        return data
    return {}


def _save(data: dict):
    # 加密 SMTP 密码后落盘
    email = data.get("email")
    if email and email.get("password"):
        email["password"] = encrypt_password(email["password"])
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


@router.get("/notifications", dependencies=[Depends(require_any_perm("settings"))])
def get_settings():
    return _load()


@router.put("/notifications", dependencies=[Depends(require_admin)])
def update_settings(settings: NotificationSettings):
    data = _load()
    if settings.dingtalk is not None:
        data["dingtalk"] = [h.model_dump() for h in settings.dingtalk]
    if settings.email is not None:
        data["email"] = settings.email.model_dump() if settings.email else None
    _save(data)
    return {"ok": True}


@router.post("/notifications/test")
def test_notification(body: dict):
    """发送测试通知 - 使用请求中传入的配置，无需先保存"""
    typ = body.get("type", "")

    if typ == "dingtalk":
        hooks = body.get("hooks", [])
        if not hooks:
            hooks = _load().get("dingtalk", [])
            hooks = [h for h in hooks if h.get("enabled") and h.get("url")]
        if not hooks:
            return {"ok": False, "msg": "没有启用的钉钉 Webhook"}
        for hook in hooks[:1]:
            try:
                import urllib.request, json
                payload = json.dumps({
                    "msgtype": "text",
                    "text": {"content": "[监控平台] 测试消息 - 告警通知渠道验证通过"}
                }, ensure_ascii=False).encode()
                req = urllib.request.Request(hook["url"], data=payload, headers={"Content-Type": "application/json"})
                resp = urllib.request.urlopen(req, timeout=5)
                result = json.loads(resp.read())
                if result.get("errcode") == 0:
                    return {"ok": True, "msg": f"发送成功 ({hook['name'] or hook['url'][:30]}...)"}
                return {"ok": False, "msg": str(result.get("errmsg", "unknown"))}
            except Exception as e:
                return {"ok": False, "msg": str(e)[:200]}
        return {"ok": False, "msg": "发送失败"}

    elif typ == "email":
        cfg = body.get("email", {}) or _load().get("email", {})
        if not cfg or not cfg.get("host"):
            return {"ok": False, "msg": "未配置 SMTP 服务器"}
        try:
            import smtplib
            from email.mime.text import MIMEText
            msg = MIMEText("[监控平台] 测试邮件 - 告警通知渠道验证通过", "plain", "utf-8")
            msg["Subject"] = "[监控平台] 测试通知"
            msg["From"] = cfg["user"]
            msg["To"] = cfg["to"]
            if cfg["port"] == 465:
                server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10)
            else:
                server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=10)
                server.starttls()
            server.login(cfg["user"], cfg["password"])
            recipients = [a.strip() for a in cfg["to"].split(",") if a.strip()]
            server.sendmail(cfg["user"], recipients, msg.as_string())
            server.quit()
            return {"ok": True, "msg": "发送成功"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    return {"ok": False, "msg": "未知通知类型"}
