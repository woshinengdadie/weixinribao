"""
消息摘要模块 - LLM 智能总结 / 关闭时不总结
LLM 模式：分析聊天记录，输出 工作感悟 + 风险识别 + 智能待办
支持远程 API（OpenAI 兼容）和本地小模型（Qwen2.5-0.5B）两种模式
关闭模式：返回空结构
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _parse_llm_json(text: str) -> Optional[Dict]:
    """从小模型输出中提取并解析 JSON，带自动修复常见格式错误。

    小模型（0.5B 级）常输出非标准 JSON：
      - 多余的回车换行、尾部逗号
      - 英文字段名被错误翻译
      - 前后有多余文字

    策略：逐步降级尝试。
    """
    if not text:
        return None

    def _try_extract_and_parse(txt: str) -> Optional[Dict]:
        """尝试从文本中提取 {} 块并解析，失败时尝试修复"""
        # 去掉 ```json 包裹
        clean = txt
        for marker in ("```json", "```"):
            if marker in clean:
                clean = clean.split(marker, 1)[1]
                if "```" in clean:
                    clean = clean.split("```", 1)[0]
                break

        # 找到最外层 {} 块
        start = clean.find("{")
        end = clean.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        json_block = clean[start:end]

        # 尝试 1: 直接解析
        try:
            return json.loads(json_block)
        except json.JSONDecodeError:
            pass

        # 尝试 2: 修复常见错误
        fixed = _repair_json(json_block)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # 尝试 3: 用正则提取关键字段（绝望模式）
        return _regex_extract(clean)

    return _try_extract_and_parse(text)


def _repair_json(text: str) -> str:
    """修复小模型 JSON 常见语法错误"""
    import re

    t = text

    # 1. 去掉尾部多余逗号（如 "key": "val", } → "key": "val" }）
    t = re.sub(r',\s*}', '}', t)
    t = re.sub(r',\s*]', ']', t)

    # 2. 修复 `\\n` 在字符串值中未转义的问题（模型有时直接用 \n）
    #    先不处理，交给 json.loads 的报错定位

    # 3. 修复中文引号
    t = t.replace('\u201c', '"').replace('\u201d', '"')
    t = t.replace('\u2018', "'").replace('\u2019', "'")

    # 4. 去掉连续逗号
    t = re.sub(r',\s*,', ',', t)

    return t


def _regex_extract(text: str) -> Optional[Dict]:
    """正则降级提取：从小模型乱序输出中拉出关键字段"""
    import re

    result = {"insights": "", "risk_points": [], "todos": []}

    # 提取 insights
    m = re.search(r'"insights"\s*:\s*"([^"]*)"', text)
    if m:
        result["insights"] = m.group(1)
    else:
        # 尝试找 insights 后面的段落（模型可能没加引号）
        m = re.search(r'(?:insights|感悟|总结)[：:]\s*(.+?)(?=\n\n|\n"|\Z)', text, re.DOTALL)
        if m:
            result["insights"] = m.group(1).strip().strip('"')

    # 提取 todos 中的 title
    titles = re.findall(r'"title"\s*:\s*"([^"]*)"', text)
    for t in titles[:20]:
        result["todos"].append({"title": t, "chat": "", "person": "", "context": "", "priority": "中", "deadline": ""})

    # 提取 risks
    risks = re.findall(r'"risk"\s*:\s*"([^"]*)"', text)
    for r in risks[:5]:
        result["risk_points"].append({"risk": r, "level": "中", "chat": "", "suggestion": ""})

    if result["insights"] or result["todos"] or result["risk_points"]:
        logger.warning("[Summarizer] 使用正则降级提取（JSON 解析失败）")
        return result
    return None


class Summarizer:
    """消息摘要生成器"""

    def __init__(self, config: dict):
        self._config = config
        self._llm_config = config.get("llm", {})
        self._llm_summary_enabled = config.get("llm_summary_enabled", True)
        self._llm_enabled = self._llm_config.get("enabled", False)
        self._client = None
        self._use_local = False
        self._local_llm = None
        self._load_error = None

        if self._llm_enabled and self._llm_summary_enabled:
            # 判断是否使用本地模型
            local_cfg = self._llm_config.get("local_model", {})
            use_local = local_cfg.get("enabled", False)

            if use_local:
                self._init_local_llm(config)
            else:
                self._init_remote_llm(self._llm_config)

    def _init_local_llm(self, config: dict):
        """初始化本地小模型"""
        try:
            from local_llm import LocalLLM

            lc = LocalLLM.get_instance(config)
            if lc and lc.is_ready():
                self._local_llm = lc
                self._use_local = True
                logger.info("[Summarizer] ✓ 本地 LLM 已就绪 (Qwen2.5-0.5B)")
            else:
                err = lc.get_load_error() if lc else "LocalLLM 实例为空"
                logger.error("[Summarizer] ❌ 本地 LLM 初始化失败: %s", err)
                # 不关闭 _llm_enabled，让 summarize 方法能返回具体错误
                self._load_error = err
        except Exception as e:
            logger.error("[Summarizer] 本地 LLM 初始化异常: %s", e)
            self._load_error = str(e)

    def _init_remote_llm(self, llm_config: dict):
        """初始化远程 API 客户端"""
        try:
            from openai import OpenAI

            api_key = llm_config.get("api_key", "")
            base_url = llm_config.get("base_url", "")

            if not api_key:
                logger.warning("LLM API Key 未配置，将不使用 AI 总结")
                self._llm_enabled = False
                return

            if not base_url:
                logger.warning("LLM Base URL 未配置，将不使用 AI 总结")
                self._llm_enabled = False
                return

            self._client = OpenAI(api_key=api_key, base_url=base_url)
            logger.info(f"LLM 客户端已初始化: {base_url}")
        except ImportError:
            logger.warning("openai 库未安装，将不使用 AI 总结")
            self._llm_enabled = False
        except Exception as e:
            logger.error(f"LLM 初始化失败: {e}")
            self._llm_enabled = False

    def _empty_summary(self, reason: str = "") -> Dict:
        """返回空的摘要"""
        return {
            "todos": [],
            "risk_points": [],
            "insights": "",
        }

    def _build_llm_prompt(self, analysis: Dict, date_str: str,
                          local_mode: bool = False) -> str:
        """构建 LLM 提示词
        local_mode=True: 本地模型只输出心得+风险（待办由规则处理）
        local_mode=False: 远程大模型完整输出
        """
        chats = analysis.get("my_chats", [])
        unreplied = analysis.get("unreplied", [])
        mentioned = analysis.get("mentioned_me", [])

        # 构建对话上下文
        conversations = []
        for chat_info in chats:
            chat_name = chat_info.get("chat", "未知")
            conv = chat_info.get("conversation", "")
            if conv:
                if len(conv) > 3000:
                    conv = conv[:3000] + "\n...(已截断)"
                conversations.append(f"### {chat_name}\n{conv}")

        # @我 / 需要注意的消息
        attention_items = []
        for item in unreplied:
            chat = item.get("chat", "")
            msg = item.get("message", {})
            content = msg.get("content", "")
            sender = msg.get("sender", "")
            attention_items.append(f"- [{chat}] {sender}: {content[:200]} (我未回复)")

        for mention_item in mentioned:
            m_chat = mention_item.get("chat", "")
            m_content = mention_item.get("content", "")
            m_sender = mention_item.get("sender", "")
            attention_items.append(f"- [{m_chat}] {m_sender}: {m_content[:200]} (@了我)")

        chat_count = len(conversations)

        # 用户自定义的规则指令
        rule_prompt = self._config.get("rule_prompt", "")
        if rule_prompt:
            # 有自定义规则：用它替换默认分析任务
            analysis_task = f"""## 分析任务（遵循用户自定义规则）
{rule_prompt}

此外，请将结果按以下 JSON 格式输出："""
        elif local_mode:
            analysis_task = """根据上面的聊天记录，完成两件事后用JSON输出：

1. insights: 用2-4句话概括我今天的工作内容和进展
2. risk_points: 识别风险或问题，没有就返回空数组"""
        else:
            analysis_task = """## 分析任务
请仔细阅读上面所有聊天记录，理解对话的来龙去脉，然后完成以下分析：

### 1. 工作感悟
从聊天记录中提炼今天的工作要点和感悟。要具体、有信息量，像真正的日报感悟那样写。
- 内容要求：今天主要参与了哪些工作讨论？取得了什么进展？遇到了什么问题？
- 格式：2-4句话的自然语言段落，不要分点，不要空洞的套话

### 2. 风险识别
从聊天中识别可能存在的风险、问题或隐患。如果确实没有风险，返回空数组。
- 每条风险要写清楚：什么事、可能的影响、严重程度
- 不要为了凑数而编造风险，没有就是没有

### 3. 待办事项
根据聊天上下文，智能识别我需要去做的事情。要具体、可执行。
- 从对话中提取：别人安排给我的任务、讨论中产生的行动项、需要我跟进的事项
- 每条待办要包含：具体做什么、从哪个群来的、谁安排的、优先级、截止时间（如果有的话）

请将结果按以下 JSON 格式输出："""

        if local_mode:
            output_fmt = '输出格式示例（严格按此格式，只输出JSON）：{"insights":"工作概括","risk_points":[{"risk":"风险","level":"高/中/低","chat":"群名","suggestion":"建议"}]}'
        else:
            output_fmt = """## 输出格式
请以 JSON 格式返回，包含以下字段：
{
  "insights": "工作感悟文字，2-4句话，自然语言",
  "risk_points": [
    {"risk": "风险描述（要具体）", "level": "高/中/低", "chat": "来源群聊", "suggestion": "建议应对措施"}
  ],
  "todos": [
    {"title": "待办事项（具体可执行）", "chat": "来源群聊", "person": "安排人（可选）", "context": "上下文说明", "priority": "高/中/低", "deadline": "截止时间（没有则为空字符串）"}
  ]
}

注意：
- insights 必须是自然语言段落，不要分点
- risk_points 如果确实没有风险，返回空数组 []
- todos 只提取真正需要我去做的事，不要无中生有
- 只返回 JSON，不要任何其他文字。"""

        if local_mode:
            header = f"以下是我的微信聊天记录（{date_str}，{chat_count}个会话）："
        else:
            header = f"""你是我的工作助手。请基于以下 {date_str} 的微信聊天记录，帮我做深度分析。

## 关于我的聊天记录（共 {chat_count} 个会话）"""

        prompt = f"""{header}
{"\n\n".join(conversations) if conversations else "（暂无对话）"}

{analysis_task}
{output_fmt}"""

        if not local_mode:
            # 远程模式追加需要注意的消息
            prompt += f"\n\n## 需要注意的消息\n{chr(10).join(attention_items) if attention_items else '（无）'}"

        # 调试 dump：将 prompt 保存到分析数据目录
        try:
            import os as _os
            analysis_dir = analysis.get("_output_dir", "")
            if analysis_dir:
                dump_path = _os.path.join(analysis_dir, "_llm_prompt_debug.md")
                with open(dump_path, "w", encoding="utf-8") as _f:
                    _f.write(prompt)
                logger.info(f"[Summarizer] Prompt 已保存到 {dump_path} ({len(prompt)} 字符)")
        except Exception:
            pass

        return prompt

    def _summarize_with_local_llm(self, analysis: Dict, date_str: str) -> Dict:
        """本地小模型总结：只输出心得 + 风险（待办由规则处理）"""
        if not self._local_llm:
            result = self._empty_summary("本地 LLM 未就绪")
            result["_llm_error"] = "本地模型未初始化"
            return result

        try:
            temperature = self._llm_config.get("temperature", 0.3)
            shared_max_tokens = self._llm_config.get("max_tokens", 4000)
            prompt = self._build_llm_prompt(analysis, date_str, local_mode=True)
            # 留够 prompt 的空间（中文约 2 字符/token，加 1024 余量）
            n_ctx = self._llm_config.get("local_model", {}).get("max_context", 16384)
            max_tokens = min(shared_max_tokens, n_ctx - len(prompt) // 2 - 1024, 4096)
            max_tokens = max(max_tokens, 256)  # 至少保留 256

            logger.info("[Summarizer] 正在用本地模型推理（prompt=%d字符, max_tokens=%d）...", len(prompt), max_tokens)
            response = self._local_llm.chat(
                messages=[
                    {"role": "system", "content": "你是专业的工作日报助手。请认真分析聊天记录，提取工作感悟、识别风险、总结待办事项。只返回 JSON，不要任何其他文字。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )

            if not response:
                logger.warning("[Summarizer] 本地 LLM 返回为空")
                result = self._empty_summary("本地 LLM 返回为空")
                result["_llm_error"] = "本地模型无返回"
                return result

            content = response
            logger.info("[Summarizer] 本地模型返回长度: %d 字符", len(content))

            summary = _parse_llm_json(content)
            if summary is None:
                logger.warning("[Summarizer] 本地模型 JSON 解析失败，原始前300: %s", content[:300])
                result = self._empty_summary("本地模型返回格式错误")
                result["_llm_error"] = "本地模型返回无法解析为 JSON"
                return result

            return {
                "todos": summary.get("todos", []),
                "risk_points": summary.get("risk_points", []),
                "insights": summary.get("insights", ""),
            }

        except Exception as e:
            logger.error("本地 LLM 总结失败: %s", e)
            result = self._empty_summary(f"本地 LLM 总结失败: {e}")
            result["_llm_error"] = f"本地模型调用失败: {e}"
            return result

    def _summarize_with_llm(self, analysis: Dict, date_str: str) -> Dict:
        """远程 API LLM 智能总结：感悟 + 风险 + 待办"""
        if not self._client:
            result = self._empty_summary("LLM 未配置")
            result["_llm_error"] = "LLM 客户端未初始化"
            return result

        try:
            model = self._llm_config.get("model", "")
            temperature = self._llm_config.get("temperature", 0.5)
            max_tokens = self._llm_config.get("max_tokens", 16000)
            prompt = self._build_llm_prompt(analysis, date_str)

            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是专业的工作日报助手。请认真分析聊天记录，提取工作感悟、识别风险、总结待办事项。感悟要像真正的日报那样写，不要套话空话。没有风险就返回空数组。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=120,  # 2 分钟超时，防止卡死导致线程永久挂起
            )

            content = response.choices[0].message.content

            # 检测 LLM 返回为空
            if not content or not content.strip():
                logger.warning("[Summarizer] LLM 返回消息为空")
                result = self._empty_summary("LLM 返回消息为空")
                result["_llm_error"] = "LLM 返回消息为空，请检查 API Key 和服务状态"
                return result

            # 记录 LLM 返回的原始内容前 300 字符用于诊断
            logger.info(f"[Summarizer] LLM 返回长度: {len(content)} 字符")
            logger.debug(f"[Summarizer] LLM 返回前300: {content[:300]}")

            # 提取 JSON（兼容 ```json ... ``` 包裹的情况）
            json_text = content
            if "```json" in json_text:
                json_text = json_text.split("```json", 1)[1]
                if "```" in json_text:
                    json_text = json_text.split("```", 1)[0]
            elif "```" in json_text:
                json_text = json_text.split("```", 1)[1]
                if "```" in json_text:
                    json_text = json_text.split("```", 1)[0]

            json_start = json_text.find("{")
            json_end = json_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                summary = json.loads(json_text[json_start:json_end])
            else:
                summary = json.loads(json_text)

            return {
                "todos": summary.get("todos", []),
                "risk_points": summary.get("risk_points", []),
                "insights": summary.get("insights", ""),
            }

        except Exception as e:
            logger.error(f"LLM 总结失败: {e}")
            result = self._empty_summary(f"LLM 总结失败: {e}")
            result["_llm_error"] = f"LLM API 调用失败: {e}"
            return result

    def summarize(self, analysis: Dict, date_str: str) -> Dict:
        """基于参与度分析生成摘要

        Args:
            analysis: message_filter.analyze() 的输出
            date_str: 日期

        Returns:
            { "todos": [...], "risk_points": [...], "insights": str }
        """
        # 判断是否有内容需要总结：我发言了、或者有未回复/提到我的消息
        has_my_msg = analysis.get("my_count", 0) > 0
        has_unreplied = len(analysis.get("unreplied", [])) > 0
        has_mentioned = len(analysis.get("mentioned_me", [])) > 0
        if not analysis or (not has_my_msg and not has_unreplied and not has_mentioned):
            return self._empty_summary("今天暂无需要总结的聊天记录")

        logger.info(
            f"Summarizer: my_count={analysis.get('my_count',0)}, "
            f"unreplied={len(analysis.get('unreplied',[]))}, "
            f"mentioned={len(analysis.get('mentioned_me',[]))}"
        )

        if self._llm_enabled and self._llm_summary_enabled:
            if self._use_local and self._local_llm:
                return self._summarize_with_local_llm(analysis, date_str)
            elif self._use_local:
                # 本地模型已启用但未就绪：提供具体错误信息
                err = self._load_error or "本地模型未就绪"
                logger.error("[Summarizer] 本地模型不可用: %s", err)
                return {
                    "todos": [],
                    "risk_points": [],
                    "insights": "",
                    "_llm_error": f"本地模型加载失败: {err}",
                }
            elif self._client:
                return self._summarize_with_llm(analysis, date_str)
            else:
                return self._empty_summary("AI 总结未开启（LLM 客户端未初始化）")
        else:
            return self._empty_summary("AI 总结未开启")
