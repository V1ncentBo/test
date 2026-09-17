import os
"""钉钉机器人告警通知服务"""
import json
import time
import hmac
import hashlib
import base64
import urllib.parse
import urllib.request
from config import bj_now, PLATFORM_PUBLIC_URL

# 告警冷却期（秒）：同机器同指标在冷却期内不重复推送
COOLDOWN_SECONDS = 1800  # 30 分钟

# 钉钉机器人 Webhook
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")

# 冷却缓存：key = "machine_id:alert_type" -> timestamp (持久化到文件)
import os
import json

# 冷却缓存持久化文件
COOLDOWN_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cooldown_cache.json")

def _load_cooldown_cache() -> dict:
    """从文件加载冷却缓存"""
    try:
        if os.path.exists(COOLDOWN_CACHE_FILE):
            with open(COOLDOWN_CACHE_FILE, "r") as f:
                data = json.load(f)
            # 清理超过冷却期的过期记录
            now = time.time()
            return {k: v for k, v in data.items() if now - v < COOLDOWN_SECONDS}
    except Exception:
        pass
    return {}

def _save_cooldown_cache(cache: dict):
    """保存冷却缓存到文件"""
    try:
        os.makedirs(os.path.dirname(COOLDOWN_CACHE_FILE), exist_ok=True)
        with open(COOLDOWN_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass

_cooldown_cache: dict[str, float] = _load_cooldown_cache()

# 平台访问地址（动态获取，迁移到新机器只需改 .env 的 PLATFORM_PUBLIC_URL）
PLATFORM_URL = PLATFORM_PUBLIC_URL or "http://localhost"


def _sign_url() -> str:
    """加签：返回带 timestamp 和 sign 参数的完整 URL"""
    timestamp = str(round(time.time() * 1000))
    secret_enc = DINGTALK_SECRET.encode("utf-8")
    string_to_sign = timestamp + chr(10) + DINGTALK_SECRET
    string_to_sign_enc = string_to_sign.encode("utf-8")
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code).decode())
    return f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"


def _is_cooldown(machine_id: int, alert_type: str) -> bool:
    """检查是否在冷却期内"""
    key = f"{machine_id}:{alert_type}"
    last_time = _cooldown_cache.get(key, 0)
    now = time.time()
    if now - last_time < COOLDOWN_SECONDS:
        return True
    _cooldown_cache[key] = now
    _save_cooldown_cache(_cooldown_cache)
    return False


def send_alert(machine_name: str, machine_ip: str, machine_id: int,
               alert_type: str, alert_level: str, metric_name: str,
               current_value: float, threshold_value: float) -> bool:
    """发送钉钉告警消息，成功返回 True"""
    if _is_cooldown(machine_id, alert_type):
        return False

    # 构建消息体
    icon = "🚨" if alert_level == "critical" else "⚠️"
    level_text = "严重" if alert_level == "critical" else "警告"
    now_str = bj_now().strftime("%Y-%m-%d %H:%M:%S")

    markdown_text = (
        f"## {icon} 监控告警 — {level_text}\n\n"
        f"**设备名称**\n"
        f"> {machine_name}\n\n"
        f"**IP 地址**\n"
        f"> {machine_ip}\n\n"
        f"**告警类型**\n"
        f"> {metric_name}\n\n"
        f"**当前值**\n"
        f"> <font color=\"#FF4D4F\">{current_value}%</font>\n\n"
        f"**阈值**\n"
        f"> {threshold_value}%\n\n"
        f"**触发时间**\n"
        f"> {now_str}\n\n"
        f"[查看详情 →]({PLATFORM_URL}/monitor/{machine_id})"
    )

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": f"{level_text} - {machine_name} {metric_name}",
            "text": markdown_text,
        },
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        url = _sign_url()  # 加签
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read())
        if result.get("errcode") == 0:
            print(f"[DingTalk] 告警推送成功: {machine_name} {metric_name}")
            return True
        else:
            print(f"[DingTalk] 推送失败: {result}")
            return False
    except Exception as e:
        print(f"[DingTalk] 推送异常: {e}")
        return False

