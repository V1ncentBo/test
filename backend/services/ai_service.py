"""AI 大模型分析服务 - 优先使用在线大模型，失败时降级到本地分析引擎
支持运行时动态切换模型与 API Key"""
import json
import re
import logging
from config import AI_API_KEY, AI_MODEL, AI_BASE_URL, bj_now
from services.local_analyzer import LocalAnalyzer

logger = logging.getLogger("ai_service")

# 全局状态 — 可通过 reload_ai_config() 热切换
_OPENAI_AVAILABLE = False
_client = None
_model = AI_MODEL
_base_url = AI_BASE_URL
_current_api_key = AI_API_KEY


def _init_client(api_key: str = None, base_url: str = None):
    """初始化 OpenAI 客户端"""
    global _OPENAI_AVAILABLE, _client, _current_api_key, _base_url
    
    key = api_key or _current_api_key
    url = base_url or _base_url
    
    if not key or key in ("sk-your-key-here", "", "***"):
        _OPENAI_AVAILABLE = False
        _client = None
        logger.warning("[AI] 未配置有效 API Key，使用本地分析引擎")
        return False
    
    try:
        from openai import OpenAI
        _client = OpenAI(api_key=key, base_url=url, timeout=30)
        _OPENAI_AVAILABLE = True
        _current_api_key = key
        _base_url = url
        logger.info(f"[AI] 在线大模型已启用: {_model} @ {url}")
        return True
    except Exception as e:
        _OPENAI_AVAILABLE = False
        _client = None
        logger.error(f"[AI] 在线大模型初始化失败: {e}")
        return False


def reload_ai_config(api_key: str = None, model: str = None, base_url: str = None):
    """运行时热切换 AI 配置（供 ai_settings 路由调用）"""
    global _model, _base_url, _current_api_key
    
    if model:
        _model = model
    if base_url:
        _base_url = base_url
    if api_key is not None:
        _current_api_key = api_key
    
    logger.info(f"[AI] 热切换配置: model={_model}, base_url={_base_url}")
    return _init_client()


def get_current_config() -> dict:
    """获取当前运行时配置"""
    return {
        "model": _model,
        "base_url": _base_url,
        "is_connected": _OPENAI_AVAILABLE,
    }


# 启动时初始化
_init_client()


class AIService:
    """AI 智能分析 - 故障诊断/趋势预测/优化建议/自然语言问答
    支持运行时动态切换模型，API 不可用时自动降级到本地引擎"""

    def __init__(self):
        # 不再在 __init__ 中缓存客户端引用，每次使用时动态读取全局变量
        pass

    @property
    def client(self):
        """动态获取客户端（支持热切换）"""
        return _client

    @property
    def model(self):
        """动态获取模型名（支持热切换）"""
        return _model

    SYSTEM_PROMPT = """你是一位拥有15年经验的资深运维架构师和系统可靠性工程师(SRE)。
你的职责是分析服务器监控数据，提供专业的运维诊断和优化建议。

请严格按照以下格式输出，使用 Markdown 格式：

## 设备运行概况
简要总结设备的整体运行状态，关键指标情况。

## 现存异常与故障
列出当前存在的异常，逐条说明：
- **异常现象**：具体表现
- **可能原因**：根本原因分析
- **解决方案**：可执行的修复步骤

## 潜在风险预测
基于数据趋势，预测未来可能出现的风险：
- 风险项、触发条件、建议预防措施

## 运维优化建议
从资源配置、性能调优、架构改进等方面给出建议。
"""

    def _build_prompt(self, machine_info: dict, metrics_data: dict,
                      alerts: list, analysis_type: str, query: str = None) -> str:
        """构建分析 Prompt"""
        info_text = f"""
## 设备信息
- 名称: {machine_info.get('name', 'N/A')}
- IP: {machine_info.get('ip', 'N/A')}
- 类型: {machine_info.get('device_type', 'N/A')}
- 分组: {machine_info.get('group_name', 'N/A')}
"""

        metrics_text = "## 实时监控数据\n"
        if metrics_data:
            keys_map = {
                "cpu_percent": ("CPU使用率", "%"),
                "memory_percent": ("内存使用率", "%"),
                "disk_percent": ("磁盘使用率", "%"),
                "load_1m": ("1分钟负载", ""),
                "load_5m": ("5分钟负载", ""),
                "load_15m": ("15分钟负载", ""),
                "network_in_mbps": ("网络入流量", "Mbps"),
                "network_out_mbps": ("网络出流量", "Mbps"),
                "disk_read_mbps": ("磁盘读", "MB/s"),
                "disk_write_mbps": ("磁盘写", "MB/s"),
                "uptime_seconds": ("运行时长", "秒"),
                "process_count": ("进程数", ""),
            }
            for key, (label, unit) in keys_map.items():
                val = metrics_data.get(key, 0)
                metrics_text += f"- {label}: {val}{unit}\n"

        alert_text = "## 近期告警\n"
        if alerts:
            for a in alerts[:10]:
                alert_text += f"- [{a.get('alert_level','')}] {a.get('message','')}\n"
        else:
            alert_text += "无告警记录\n"

        task_desc = {
            "full": "请完成完整的四模块分析：设备运行概况、现存异常与故障、潜在风险预测、运维优化建议。",
            "diagnosis": "请重点分析当前设备是否存在故障，如存在请给出诊断结果和解决方案。",
            "prediction": "请基于当前监控数据，预测未来24小时可能出现的问题。",
            "optimization": "请重点给出资源配置和性能优化建议。",
        }

        user_query = f"\n## 用户提问\n{query}\n" if query else ""

        return f"""{info_text}
{metrics_text}
{alert_text}
{task_desc.get(analysis_type, task_desc['full'])}
{user_query}"""

    def analyze(self, machine_info: dict, metrics_data: dict,
                alerts: list, analysis_type: str = "full", query: str = None) -> dict:
        """执行 AI 分析 - API 不可用时自动降级到本地引擎"""
        # 优先使用在线大模型
        if _OPENAI_AVAILABLE and _client:
            try:
                prompt = self._build_prompt(machine_info, metrics_data, alerts, analysis_type, query)
                response = _client.chat.completions.create(
                    model=_model,
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=2048,
                )
                content = response.choices[0].message.content
                result = {
                    "success": True,
                    "raw": content,
                    "model_used": _model,
                    "overview": self._extract_section(content, "设备运行概况"),
                    "diagnosis": self._extract_section(content, "现存异常与故障"),
                    "prediction": self._extract_section(content, "潜在风险预测"),
                    "optimization": self._extract_section(content, "运维优化建议"),
                }
                # 根据分析类型过滤
                if analysis_type == "diagnosis":
                    result["overview"] = ""; result["prediction"] = ""; result["optimization"] = ""
                elif analysis_type == "prediction":
                    result["overview"] = ""; result["diagnosis"] = ""; result["optimization"] = ""
                elif analysis_type == "optimization":
                    result["overview"] = ""; result["diagnosis"] = ""; result["prediction"] = ""
                return result
            except Exception as e:
                logger.warning(f"AI model {self.model} failed: {e}, falling back to local analyzer")
                return LocalAnalyzer.analyze(machine_info, metrics_data, alerts, analysis_type, query)

        # 降级：在线模型不可用，使用本地分析引擎
        return LocalAnalyzer.analyze(machine_info, metrics_data, alerts, analysis_type, query)

    def chat(self, context: dict, user_query: str) -> str:
        """自然语言问答 - API 不可用时降级到本地引擎"""
        if _OPENAI_AVAILABLE and _client:
            try:
                response = _client.chat.completions.create(
                    model=_model,
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": f"参考数据：{json.dumps(context, ensure_ascii=False)}\n\n问题：{user_query}"}
                    ],
                    temperature=0.5,
                    max_tokens=1024,
                )
                return response.choices[0].message.content
            except Exception as e:
                logger.warning(f"[AI] 在线问答失败: {e}，降级到本地")

        return LocalAnalyzer.chat(context, user_query)

    def generate_report(self, all_machines: list, period: str = "daily") -> str:
        """生成运维报告 - API 不可用时降级到本地引擎"""
        if _OPENAI_AVAILABLE and _client:
            try:
                now = bj_now().strftime("%Y-%m-%d %H:%M")
                today = bj_now().strftime("%Y-%m-%d")
                prompt = f"""当前时间: {now} (北京时间)
请基于以下 {len(all_machines)} 台设备的监控概况，生成一份{period}运维报告（报告日期: {today}）。
用中文输出，简洁专业，直接说重点不需要客套话。

设备数据：
{json.dumps(all_machines, ensure_ascii=False, default=str)[:4000]}

按以下结构输出：
## 运行概况
（设备总数、在线数、平均 CPU/内存/磁盘使用率，1-2句话）

## 异常与告警
（列出有告警或指标异常的机器，说明问题，无异常则写"本期运行正常"）

## 资源分析
（逐台简述资源使用状况，关键指标是否正常，每台一行）

## 优化建议
（根据数据给出 1-3 条具体可执行的建议）"""
                response = _client.chat.completions.create(
                    model=_model,
                    messages=[
                        {"role": "system", "content": "你是资深运维工程师，用中文回复，简洁专业，只说重点不啰嗦。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=2048,
                )
                return response.choices[0].message.content
            except Exception as e:
                logger.warning(f"[AI] 在线报告生成失败: {e}，降级到本地")

        return LocalAnalyzer.generate_report(all_machines, period)

    @staticmethod
    def _extract_section(text: str, section_name: str) -> str:
        """从 Markdown 文本提取指定段落"""
        pattern = rf'##\s*{section_name}\s*\n(.*?)(?=##\s|\Z)'
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""


ai_service = AIService()
