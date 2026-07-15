"""
重点会话分析模块 — 独立于日报功能
- 手动指定要分析的会话
- 无屏蔽，无过滤，全部消息保留
- 全方面 LLM 分析：话题、决策、人员、进度、待办、风险
"""
import json
import logging
import os
import sys
import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

PAGE_SZ = 4096


def _extract_json(text):
    """从混合文本中提取有效 JSON（复用 wechat_reader 的逻辑）"""
    import re as _re
    if not text:
        return None
    text = text.strip().lstrip('\ufeff')
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    # 尝试找 {} 块
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '"':
            # 简单跳过字符串（可能不完全准确，但够用）
            pass
        elif ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                block = text[start:i + 1]
                try:
                    json.loads(block)
                    return block
                except json.JSONDecodeError:
                    pass
                start = -1
    return None


class ChatAnalyzer:
    """重点会话分析器"""

    def __init__(self, config: dict):
        self._config = config
        self._client = None
        self._local_llm = None
        self._use_local = False
        self._llm_config = config.get("llm", {})
        # 复用 WeChatReader 实例，避免每次调用都重新初始化
        from wechat_reader import WeChatReader
        self._reader = WeChatReader(config)
        self._init_llm()

    def _init_llm(self):
        """初始化 LLM（远程 API 或本地模型）"""
        # 判断是否使用本地模型
        local_cfg = self._llm_config.get("local_model", {})
        if local_cfg.get("enabled", False):
            try:
                from local_llm import LocalLLM
                lc = LocalLLM.get_instance(self._config)
                if lc and lc.is_ready():
                    self._local_llm = lc
                    self._use_local = True
                    logger.info("ChatAnalyzer: 使用本地 LLM")
                    return
                else:
                    err = lc.get_load_error() if lc else "None"
                    logger.warning("ChatAnalyzer: 本地 LLM 不可用: %s", err)
            except Exception as e:
                logger.error("ChatAnalyzer: 本地 LLM 初始化失败: %s", e)
            return

        # 远程 API 模式
        try:
            from openai import OpenAI
            api_key = self._llm_config.get("api_key", "")
            base_url = self._llm_config.get("base_url", "")
            if not api_key or not base_url:
                logger.warning("ChatAnalyzer: LLM 未配置")
                return
            self._client = OpenAI(api_key=api_key, base_url=base_url)
        except Exception as e:
            logger.error(f"ChatAnalyzer: LLM 初始化失败: {e}")

    def _get_sessions(self) -> List[Dict]:
        """获取所有会话列表（用于前端选择器）"""
        sessions = self._reader._get_sessions_cli()
        results = []
        for s in sessions:
            name = (s.get("chat") or s.get("name", "")).strip()
            if name:
                uname = s.get("username", "")
                is_group = s.get("is_group", False) or "@chatroom" in uname
                results.append({
                    "name": name,
                    "username": uname,
                    "is_group": is_group,
                    "last_time": s.get("time", ""),
                })
        return results

    def _get_chat_history(self, chat_name: str, since: datetime, limit: int = 500) -> List[str]:
        """获取指定会话的原始消息文本"""
        from wechat_reader import _extract_json as ext_json
        since_str = since.strftime("%Y-%m-%d %H:%M:%S")
        output = self._reader._run_wechat_cli_inprocess(
            "history", chat_name,
            "--start-time", since_str,
            "--limit", str(limit),
            "--format", "json",
        )
        if not output:
            logger.warning(f"ChatAnalyzer: [{chat_name}] 无输出")
            return []

        extracted = ext_json(output)
        if not extracted:
            logger.warning(f"ChatAnalyzer: [{chat_name}] JSON 提取失败")
            return []

        try:
            data = json.loads(extracted)
        except json.JSONDecodeError:
            logger.warning(f"ChatAnalyzer: [{chat_name}] JSON 解析失败")
            return []

        if isinstance(data, dict):
            raw_msgs = data.get("messages", [])
            count = data.get("count", len(raw_msgs))
            logger.info(f"ChatAnalyzer: [{chat_name}] 获取 {count} 条消息")
            if isinstance(raw_msgs, list):
                return [str(m) for m in raw_msgs]
        return []

    def _build_prompt(self, chat_name: str, messages: List[str], date_str: str,
                      requirement: Optional[str] = None) -> str:
        """构建 LLM 分析 prompt"""
        joined = "\n".join(messages)
        if len(joined) > 8000:
            joined = joined[:8000] + "\n...(消息过长已截断)"

        if requirement:
            # 用户自定义分析要求
            analysis_instruction = requirement
            output_format = """## 输出格式
请以 JSON 格式返回完整分析结果：
{
  "topics": ["话题1", "话题2"],
  "decisions": ["决策1", "决策2"],
  "participants": [{"name": "人名", "role": "角色/做了什么", "key_points": ["发言要点"]}],
  "todos": [{"title": "待办事项", "person": "负责人", "priority": "高/中/低", "deadline": "截止时间"}],
  "risks": [{"risk": "风险描述", "level": "高/中/低", "suggestion": "建议"}],
  "summary": "一段话总结"
}

注意：所有字段尽量填全，没有就返回空数组 []，summary 必填。
只返回 JSON，不要其他任何文字。"""
        else:
            # 默认全面分析
            analysis_instruction = """请认真阅读全部聊天内容，理解对话的前后逻辑，然后输出以下分析结果：

### 1. 对话主题与关键讨论
- 本次聊天围绕哪些核心话题展开？
- 有哪些重点讨论内容？

### 2. 做出的决策与结论
- 群里做出了哪些明确的决定？
- 达成了哪些共识？

### 3. 各成员的发言要点与角色
- 谁说了什么重要的话？
- 每个人扮演的角色是什么？

### 4. 待办事项
- 聊天中产生了哪些需要后续跟进的任务？
- 分别是谁负责的？

### 5. 风险与问题
- 讨论中暴露了什么问题、风险或阻塞？
- 如果确实没有，返回空数组

### 6. 重点摘要
- 用一段话总结本次聊天最重要的信息"""
            output_format = """## 输出格式
请以 JSON 格式返回：
{{
  "topics": ["话题1", "话题2"],
  "decisions": ["决策1", "决策2"],
  "participants": [{{"name": "人名", "role": "角色/做了什么", "key_points": ["发言要点"]}}],
  "todos": [{{"title": "待办事项", "person": "负责人", "priority": "高/中/低", "deadline": "截止时间（没有则为空）"}}],
  "risks": [{{"risk": "风险描述", "level": "高/中/低", "suggestion": "建议"}}],
  "summary": "一段话总结"
}}

只返回 JSON，不要其他任何文字。"""

        return f"""请分析以下 "{chat_name}" 的聊天记录（{date_str}）。

## 聊天内容
{joined}

## 分析要求
{analysis_instruction}

{output_format}"""

    def _call_llm(self, prompt: str) -> Optional[Dict]:
        """调用 LLM 并解析 JSON 结果（支持本地模型和远程 API）"""
        if self._use_local:
            return self._call_local_llm(prompt)
        return self._call_remote_llm(prompt)

    def _call_local_llm(self, prompt: str) -> Optional[Dict]:
        """调用本地模型"""
        if not self._local_llm:
            logger.error("ChatAnalyzer: 本地 LLM 未初始化")
            return None

        try:
            # 读取共享的 max_tokens，上限不超过上下文窗口
            shared_max_tokens = self._llm_config.get("max_tokens", 4000)
            n_ctx = self._llm_config.get("local_model", {}).get("max_context", 16384)
            max_tokens = min(shared_max_tokens, n_ctx - len(prompt) // 2 - 1024, 4096)
            max_tokens = max(max_tokens, 256)

            response = self._local_llm.chat(
                messages=[
                    {"role": "system", "content": "你是专业的会议记录和对话分析助手。请认真阅读聊天记录，做全面深度分析。不要编造信息，没有的就返回空数组。只返回 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=self._llm_config.get("temperature", 0.3),
                max_tokens=max_tokens,
            )

            if not response:
                logger.warning("[ChatAnalyzer] 本地 LLM 返回为空")
                return None

            content = response
            logger.info(f"ChatAnalyzer: 本地 LLM 返回 {len(content)} 字符")

            # 使用统一 JSON 修复逻辑（支持小模型常见格式错误）
            from summarizer import _parse_llm_json
            result = _parse_llm_json(content)
            if result:
                return result

            # 回退到旧方法
            extracted = _extract_json(content)
            if not extracted:
                logger.warning(f"ChatAnalyzer: JSON 提取失败，原始: {content[:300]}")
                return None
            return json.loads(extracted)

        except Exception as e:
            logger.error(f"ChatAnalyzer: 本地 LLM 调用失败: {e}")
            return None

    def _call_remote_llm(self, prompt: str) -> Optional[Dict]:
        """调用远程 API"""
        if not self._client:
            logger.error("ChatAnalyzer: LLM 客户端未初始化")
            return None

        try:
            model = self._llm_config.get("model", "")
            temperature = self._llm_config.get("temperature", 0.5)
            max_tokens = self._llm_config.get("max_tokens", 16000)

            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是专业的会议记录和对话分析助手。请认真阅读聊天记录，做全面深度分析。不要编造信息，没有的就返回空数组。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=120,  # 2 分钟超时
            )

            content = response.choices[0].message.content
            logger.info(f"ChatAnalyzer: LLM 返回 {len(content)} 字符")

            # 检测 LLM 返回为空
            if not content or not content.strip():
                logger.warning("[ChatAnalyzer] LLM 返回消息为空")
                return None

            # 提取 JSON（去除可能的 ```json 包装）
            json_text = content
            if "```json" in json_text:
                json_text = json_text.split("```json", 1)[1]
                if "```" in json_text:
                    json_text = json_text.split("```", 1)[0]

            extracted = _extract_json(json_text)
            if not extracted:
                logger.warning(f"ChatAnalyzer: JSON 提取失败，原始: {content[:300]}")
                return None

            return json.loads(extracted)

        except Exception as e:
            logger.error(f"ChatAnalyzer: LLM 调用失败: {e}")
            return None

    def analyze(
        self,
        chat_names: List[str],
        since: datetime,
        requirement: Optional[str] = None,
        progress: Optional[Callable] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> Dict:
        """执行重点会话分析

        Args:
            chat_names: 要分析的会话列表
            since: 起始时间
            requirement: 用户自定义分析要求（留空则全面分析）
            progress: 进度回调
            stop_event: 停止事件

        Returns:
            {"success": bool, "filepath": str, "results": [...]}
        """
        def log(msg):
            if progress:
                progress(msg)
            logger.info(msg)

        def _stopped():
            return stop_event and stop_event.is_set()

        log(f"开始分析 {len(chat_names)} 个会话..."
            + (f"\n  自定义要求: {requirement[:60]}..." if requirement else "（全面分析）"))

        now = datetime.now()
        date_str = f"{since.strftime('%Y-%m-%d %H:%M')} → {now.strftime('%Y-%m-%d %H:%M')}"
        date_filename = now.strftime("%Y%m%d_%H%M%S")

        all_results = []
        for i, chat_name in enumerate(chat_names):
            if _stopped():
                log(f"⏹ 已停止，已分析 {i}/{len(chat_names)} 个会话")
                break

            log(f"[{i+1}/{len(chat_names)}] 正在获取 [{chat_name}] 的消息...")
            messages = self._get_chat_history(chat_name, since)
            if not messages:
                log(f"  [{chat_name}] 无消息")
                all_results.append({"chat": chat_name, "error": "无消息", "messages": []})
                continue

            log(f"[{i+1}/{len(chat_names)}] [{chat_name}] AI 分析中 ({len(messages)} 条消息)...")

            if self._client or self._local_llm:
                prompt = self._build_prompt(chat_name, messages, date_str, requirement=requirement)
                analysis = self._call_llm(prompt)
                if analysis is None:
                    status = "本地模型" if self._use_local else "远程 API"
                    log(f"❌ [{chat_name}] AI 分析失败：{status} 无返回或返回为空")
            else:
                analysis = None
                log(f"❌ [{chat_name}] AI 分析失败：LLM 客户端未配置")

            all_results.append({
                "chat": chat_name,
                "message_count": len(messages),
                "analysis": analysis,
                "error": None if analysis else "AI 分析失败或未配置",
                "messages": messages,
            })

        # 生成报告
        filepath = self._generate_report(all_results, date_str, date_filename, requirement=requirement)
        log(f"报告已生成: {filepath}")

        return {
            "success": True,
            "filepath": filepath,
            "results": all_results,
        }

    def _generate_report(self, results: List[Dict], date_str: str, date_filename: str,
                          requirement: Optional[str] = None) -> str:
        """生成 Markdown 分析报告"""
        output_cfg = self._config.get("output", {})
        output_dir = output_cfg.get("dir", "./output")
        ext = output_cfg.get("format", "md")
        if ext not in ("md", "txt"):
            ext = "md"
        if not os.path.isabs(output_dir):
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            output_dir = os.path.join(base, output_dir)
        os.makedirs(output_dir, exist_ok=True)

        lines = []
        lines.append(f"# 重点会话分析报告")
        lines.append(f"")
        lines.append(f"> 分析时间范围: {date_str}")
        lines.append(f"> 分析会话数: {len(results)}")
        if requirement:
            lines.append(f"> 分析要求: {requirement}")
        lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # 概述
        lines.append("## 概述")
        lines.append("")
        for r in results:
            cnt = r.get("message_count", 0)
            err = r.get("error")
            status = f"✅ {cnt} 条消息" if not err else f"⚠️ {err}"
            lines.append(f"- **{r['chat']}**: {status}")
        lines.append("")

        # 每个会话的详细分析
        for r in results:
            chat = r["chat"]
            analysis = r.get("analysis")
            msgs = r.get("messages", [])

            lines.append(f"## {chat}")
            lines.append("")

            if analysis:
                # 话题
                topics = analysis.get("topics", [])
                if topics:
                    lines.append("### 📌 核心话题")
                    lines.append("")
                    for t in topics:
                        lines.append(f"- {t}")
                    lines.append("")

                # 决策
                decisions = analysis.get("decisions", [])
                if decisions:
                    lines.append("### 📋 做出的决策")
                    lines.append("")
                    for d in decisions:
                        lines.append(f"- {d}")
                    lines.append("")

                # 参与者
                participants = analysis.get("participants", [])
                if participants:
                    lines.append("### 👥 成员发言要点")
                    lines.append("")
                    for p in participants:
                        if isinstance(p, str):
                            lines.append(f"- {p}")
                            continue
                        name = p.get("name", "未知")
                        role = p.get("role", "")
                        key_points = p.get("key_points", [])
                        lines.append(f"- **{name}** ({role})")
                        for kp in key_points:
                            lines.append(f"  - {kp}")
                    lines.append("")

                # 待办
                todos = analysis.get("todos", [])
                if todos:
                    lines.append("### ✅ 待办事项")
                    lines.append("")
                    for t in todos:
                        title = t.get("title", "")
                        person = t.get("person", "")
                        priority = t.get("priority", "中")
                        deadline = t.get("deadline", "")
                        flag = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(priority, "🟡")
                        entry = f"- {flag} {title}"
                        if person:
                            entry += f" (负责: {person})"
                        if deadline:
                            entry += f" 截止: {deadline}"
                        lines.append(entry)
                    lines.append("")

                # 风险
                risks = analysis.get("risks", [])
                if risks:
                    lines.append("### ⚠️ 风险与问题")
                    lines.append("")
                    for risk in risks:
                        r_text = risk.get("risk", "")
                        level = risk.get("level", "中")
                        suggestion = risk.get("suggestion", "")
                        flag = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(level, "🟡")
                        lines.append(f"- {flag} **[{level}风险]** {r_text}")
                        if suggestion:
                            lines.append(f"  - 建议: {suggestion}")
                    lines.append("")

                # 摘要
                summary = analysis.get("summary", "")
                if summary:
                    lines.append("### 📝 总结")
                    lines.append("")
                    lines.append(summary)
                    lines.append("")
            else:
                lines.append(f"⚠️ AI 分析失败，仅展示原始消息")
                lines.append("")
                for msg in msgs[:50]:
                    lines.append(f"- {msg}")
                if len(msgs) > 50:
                    lines.append(f"... 还有 {len(msgs) - 50} 条消息")
                lines.append("")

            lines.append("---")
            lines.append("")

        content = "\n".join(lines)
        filename = f"重点会话分析_{date_filename}.{ext}"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath
