"""
AI 模型配置 API — 支持多模型预设导入与运行时切换
═══════════════════════════════════════════════════════════
提供 API Key 配置、模型预设选择、连接测试、运行时热切换
"""
import json
import os
import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from routers.auth import get_current_user, require_any_perm

logger = logging.getLogger("ai_settings")

router = APIRouter(prefix="/api/ai-settings", tags=["AI 模型配置"], dependencies=[Depends(require_any_perm("ai"))])

# 配置文件路径（持久化到宿主机挂载卷）
AI_SETTINGS_FILE = "/app/data/ai_settings.json"

# ═══════════════════════════════════════════════════════════
#  主流 AI 模型预设库
# ═══════════════════════════════════════════════════════════

MODEL_PRESETS = [
    # ── DeepSeek ──
    {
        "provider": "DeepSeek",
        "provider_icon": "🔥",
        "models": [
            {"id": "deepseek-chat", "name": "DeepSeek-V3", "desc": "旗舰对话模型，综合能力强"},
            {"id": "deepseek-reasoner", "name": "DeepSeek-R1", "desc": "深度推理模型，擅长复杂逻辑"},
        ],
        "base_url": "https://api.deepseek.com/v1",
        "api_key_help": "在 platform.deepseek.com 获取",
        "requires_api_key": True,
    },
    # ── OpenAI ──
    {
        "provider": "OpenAI",
        "provider_icon": "🤖",
        "models": [
            {"id": "gpt-4o", "name": "GPT-4o", "desc": "最新多模态旗舰模型"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "desc": "轻量高效，性价比之选"},
            {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "desc": "高性能推理模型"},
        ],
        "base_url": "https://api.openai.com/v1",
        "api_key_help": "在 platform.openai.com 获取",
        "requires_api_key": True,
    },
    # ── 通义千问 (阿里云) ──
    {
        "provider": "通义千问",
        "provider_icon": "☁️",
        "models": [
            {"id": "qwen-max", "name": "Qwen-Max", "desc": "最强千问模型，复杂任务首选"},
            {"id": "qwen-plus", "name": "Qwen-Plus", "desc": "平衡性能与成本"},
            {"id": "qwen-turbo", "name": "Qwen-Turbo", "desc": "轻量快速，适合简单任务"},
        ],
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_help": "在 dashscope.console.aliyun.com 获取",
        "requires_api_key": True,
    },
    # ── 智谱 GLM ──
    {
        "provider": "智谱AI (GLM)",
        "provider_icon": "🧠",
        "models": [
            {"id": "glm-4-plus", "name": "GLM-4-Plus", "desc": "旗舰模型，综合性能突出"},
            {"id": "glm-4", "name": "GLM-4", "desc": "主力模型，适合大部分场景"},
            {"id": "glm-4-flash", "name": "GLM-4-Flash", "desc": "免费极速模型"},
        ],
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_help": "在 open.bigmodel.cn 获取",
        "requires_api_key": True,
    },
    # ── Moonshot (Kimi) ──
    {
        "provider": "Moonshot (Kimi)",
        "provider_icon": "🌙",
        "models": [
            {"id": "moonshot-v1-8k", "name": "Moonshot v1-8K", "desc": "标准上下文，高性价比"},
            {"id": "moonshot-v1-32k", "name": "Moonshot v1-32K", "desc": "长上下文，适合复杂分析"},
            {"id": "moonshot-v1-128k", "name": "Moonshot v1-128K", "desc": "超长上下文，全文分析"},
        ],
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_help": "在 platform.moonshot.cn 获取",
        "requires_api_key": True,
    },
    # ── 百度文心一言 ──
    {
        "provider": "百度文心一言",
        "provider_icon": "🐻",
        "models": [
            {"id": "ernie-4.0-8k", "name": "ERNIE 4.0", "desc": "文心旗舰模型"},
            {"id": "ernie-3.5-8k", "name": "ERNIE 3.5", "desc": "均衡性能模型"},
            {"id": "ernie-speed-8k", "name": "ERNIE Speed", "desc": "极速响应模型"},
        ],
        "base_url": "https://qianfan.baidubce.com/v2",
        "api_key_help": "在 console.bce.baidu.com/qianfan 获取",
        "requires_api_key": True,
    },
    # ── 字节豆包 ──
    {
        "provider": "字节豆包 (Doubao)",
        "provider_icon": "🫘",
        "models": [
            {"id": "doubao-pro-32k", "name": "豆包 Pro 32K", "desc": "主力模型，32K上下文"},
            {"id": "doubao-lite-32k", "name": "豆包 Lite 32K", "desc": "轻量快速版"},
        ],
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key_help": "在 console.volcengine.com/ark 获取",
        "requires_api_key": True,
    },
    # ── Anthropic Claude (兼容) ──
    {
        "provider": "Anthropic Claude (兼容)",
        "provider_icon": "🧪",
        "models": [
            {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet", "desc": "平衡推理与效率"},
            {"id": "claude-3-opus-20240229", "name": "Claude 3 Opus", "desc": "最强深度推理"},
            {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku", "desc": "极速轻量版"},
        ],
        "base_url": "https://api.anthropic.com/v1",
        "api_key_help": "在 console.anthropic.com 获取（需兼容网关支持）",
        "requires_api_key": True,
        "note": "Claude 原生 API 非 OpenAI 格式，需通过兼容网关（如 One API）转换",
    },
    # ── 自定义 ──
    {
        "provider": "自定义",
        "provider_icon": "⚙️",
        "models": [
            {"id": "custom-model", "name": "自定义模型", "desc": "手动输入模型名称和接口地址"},
        ],
        "base_url": "",
        "api_key_help": "输入兼容 OpenAI 格式的 API 地址和密钥",
        "requires_api_key": True,
        "is_custom": True,
    },
]


# ═══════════════════════════════════════════════════════════
#  请求/响应模型
# ═══════════════════════════════════════════════════════════

class AISettingsUpdate(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    custom_model_name: Optional[str] = None


class TestRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None


# ═══════════════════════════════════════════════════════════
#  持久化读写
# ═══════════════════════════════════════════════════════════

def _load() -> dict:
    """加载 AI 配置"""
    if os.path.exists(AI_SETTINGS_FILE):
        with open(AI_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # 返回默认值（来自 config.py）
    from config import AI_API_KEY, AI_MODEL, AI_BASE_URL
    return {
        "provider": "",
        "model": AI_MODEL,
        "base_url": AI_BASE_URL,
        "api_key": "***" if AI_API_KEY and AI_API_KEY not in ("sk-your-key-here", "") else "",
        "custom_model_name": "",
    }


def _save(data: dict):
    """保存 AI 配置（API Key 不加密，仅本地存储）"""
    os.makedirs(os.path.dirname(AI_SETTINGS_FILE), exist_ok=True)
    with open(AI_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("AI 配置已保存")


def _apply_runtime(settings: dict):
    """将配置应用到运行时（热切换 AI 客户端）"""
    from services.ai_service import reload_ai_config
    api_key = settings.get("api_key", "")
    model = settings.get("model", "")
    base_url = settings.get("base_url", "")
    
    if api_key and api_key != "***":
        reload_ai_config(api_key=api_key, model=model, base_url=base_url)
    else:
        reload_ai_config(api_key="", model=model, base_url=base_url)


# ═══════════════════════════════════════════════════════════
#  API 端点
# ═══════════════════════════════════════════════════════════

@router.get("/models", summary="获取模型预设列表")
def get_model_presets():
    """返回所有主流 AI 模型的预设配置（供前端下拉选择）"""
    return {"code": 0, "data": MODEL_PRESETS}


@router.get("/current", summary="获取当前 AI 配置")
def get_current_settings():
    """获取当前生效的 AI 配置（API Key 脱敏）"""
    data = _load()
    # 脱敏处理
    api_key = data.get("api_key", "")
    if api_key and api_key not in ("", "***"):
        if len(api_key) > 8:
            data["api_key"] = api_key[:4] + "****" + api_key[-4:]
        else:
            data["api_key"] = "***"
    
    # 附加运行时状态
    from services.ai_service import _OPENAI_AVAILABLE
    data["is_connected"] = _OPENAI_AVAILABLE
    return {"code": 0, "data": data}


@router.put("/current", summary="更新并应用 AI 配置")
def update_settings(req: AISettingsUpdate):
    """更新 AI 配置并即时生效（热切换）"""
    current = _load()
    
    if req.provider is not None:
        current["provider"] = req.provider
    if req.model is not None:
        current["model"] = req.model
    if req.base_url is not None:
        current["base_url"] = req.base_url
    if req.api_key is not None and req.api_key != "":
        current["api_key"] = req.api_key
    if req.custom_model_name is not None:
        current["custom_model_name"] = req.custom_model_name
    
    _save(current)
    
    # 热切换到新的 AI 配置
    _apply_runtime(current)
    
    # 脱敏返回
    result = dict(current)
    api_key = result.get("api_key", "")
    if api_key and len(api_key) > 8:
        result["api_key"] = api_key[:4] + "****" + api_key[-4:]
    
    from services.ai_service import _OPENAI_AVAILABLE
    result["is_connected"] = _OPENAI_AVAILABLE
    return {"code": 0, "data": result, "message": "AI 配置已更新并生效"}


@router.post("/test", summary="测试 AI 连接")
def test_connection(req: TestRequest):
    """用指定配置测试 API 连通性"""
    api_key = req.api_key
    base_url = req.base_url
    model = req.model
    
    # 如果未传参数，使用当前配置
    if not api_key or not base_url:
        current = _load()
        api_key = api_key or current.get("api_key", "")
        base_url = base_url or current.get("base_url", "")
        model = model or current.get("model", "")
    
    if not api_key or api_key in ("", "***"):
        return {"code": -1, "message": "请先配置 API Key", "success": False}
    
    try:
        from openai import OpenAI
        
        test_client = OpenAI(api_key=api_key, base_url=base_url, timeout=15)
        
        # 发送测试消息
        response = test_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": "请回复'连接成功'四个字，不要其他内容。"}
            ],
            max_tokens=20,
            temperature=0,
        )
        
        reply = response.choices[0].message.content
        latency_ms = int(response.usage.total_tokens * 10) if hasattr(response, 'usage') else 0
        
        return {
            "code": 0,
            "success": True,
            "message": f"连接成功！模型返回: {reply[:50]}",
            "model": model,
            "model_used": response.model,
        }
    except Exception as e:
        err_msg = str(e)
        # 解析常见错误
        if "401" in err_msg or "Unauthorized" in err_msg:
            err_msg = "认证失败：API Key 无效或已过期"
        elif "404" in err_msg or "not found" in err_msg.lower():
            err_msg = f"模型 '{model}' 不存在，请检查模型名称"
        elif "timeout" in err_msg.lower() or "connect" in err_msg.lower():
            err_msg = f"无法连接到 {base_url}，请检查网络和地址"
        elif "429" in err_msg:
            err_msg = "请求过于频繁，请稍后重试"
        elif "403" in err_msg:
            err_msg = "权限不足：API Key 无权访问该模型"
        
        return {"code": -1, "success": False, "message": err_msg}


@router.post("/apply-preset", summary="应用模型预设")
def apply_preset(body: dict):
    """一键应用模型预设配置"""
    provider_name = body.get("provider", "")
    model_id = body.get("model_id", "")
    api_key = body.get("api_key", "")
    
    if not provider_name or not model_id:
        raise HTTPException(status_code=400, detail="请选择模型提供商和模型")
    
    # 查找预设
    preset = None
    selected_model = None
    for p in MODEL_PRESETS:
        if p["provider"] == provider_name:
            preset = p
            for m in p["models"]:
                if m["id"] == model_id:
                    selected_model = m
                    break
            break
    
    if not preset:
        raise HTTPException(status_code=400, detail=f"未找到提供商: {provider_name}")
    if not selected_model:
        raise HTTPException(status_code=400, detail=f"未找到模型: {model_id}")
    
    # 构建配置
    base_url = preset["base_url"]
    model = selected_model["id"]
    
    # 如果是自定义，使用传入的 base_url
    if preset.get("is_custom"):
        base_url = body.get("base_url", base_url)
        model = body.get("custom_model_name", model_id)
    
    # 保存配置
    current = {
        "provider": provider_name,
        "model": model,
        "base_url": base_url,
        "api_key": api_key if api_key else "",
        "custom_model_name": body.get("custom_model_name", ""),
    }
    _save(current)
    
    # 热切换
    _apply_runtime(current)
    
    result = dict(current)
    if api_key and len(api_key) > 8:
        result["api_key"] = api_key[:4] + "****" + api_key[-4:]
    
    from services.ai_service import _OPENAI_AVAILABLE
    result["is_connected"] = _OPENAI_AVAILABLE
    
    return {
        "code": 0,
        "data": result,
        "message": f"已切换到 {provider_name} / {selected_model['name']}",
    }
