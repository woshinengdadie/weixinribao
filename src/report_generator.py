"""
日报生成模块 - 输出两个文件：
1. 工作总结_YYYY-MM-DD.md — 工作感悟 + 风险识别 + 待办事项
2. 对话详情_YYYY-MM-DD.md — 完整的对话消息时间线
"""

import os
import logging
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class ReportGenerator:
    """日报生成器"""

    def __init__(self, config: dict):
        self._config = config
        self._output_dir = config.get("output", {}).get("dir", "./output")
        self._format = config.get("output", {}).get("format", "md")
        self._include_raw = config.get("output", {}).get("include_raw", True)

    def _ext(self) -> str:
        """获取文件扩展名"""
        return self._format if self._format in ("md", "txt") else "md"

    def _write_work_summary(self, analysis: Dict, summary: Dict, date_str: str,
                             llm_summary_enabled: bool = True) -> str:
        """写入工作总结文件

        内容：工作感悟 + 风险识别 + 待办事项
        """
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            weekday = WEEKDAY_NAMES[dt.weekday()]
            date_display = f"{date_str}（{weekday}）"
        except:
            date_display = date_str

        todos = summary.get("todos", [])
        risks = summary.get("risk_points", [])
        insights = summary.get("insights", "")

        lines = []
        lines.append(f"# 微信工作日报 - {date_display}")
        lines.append("")

        my_chat_count = len(analysis.get("my_chats", []))
        my_msg_count = analysis.get("my_count", 0)
        total_msgs = analysis.get("total_messages", 0)
        unreplied_count = analysis.get("unreplied_count", 0)
        summary_line = f"> 共 {total_msgs} 条消息，你参与了 {my_chat_count} 个会话，发言 {my_msg_count} 条"
        if unreplied_count:
            summary_line += f"，{unreplied_count} 条未回复"
        lines.append(summary_line)
        lines.append("")

        # ===== 工作感悟 =====
        lines.append("## 工作感悟")
        lines.append("")
        if llm_summary_enabled and insights:
            lines.append(insights)
            lines.append("")
        elif llm_summary_enabled and not insights:
            lines.append("今天暂无特别的工作感悟。")
            lines.append("")
        else:
            lines.append("AI 总结功能已关闭，此处不生成内容。")
            lines.append("")

        # ===== 风险识别 =====
        lines.append("## 风险识别")
        lines.append("")
        if llm_summary_enabled and risks:
            for r in risks:
                level = r.get("level", "中")
                risk_text = r.get("risk", "")
                chat = r.get("chat", "")
                suggestion = r.get("suggestion", "")
                flag = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(level, "🟡")
                lines.append(f"- {flag} **[{level}风险]** [{chat}] {risk_text}")
                if suggestion:
                    lines.append(f"  - 建议: {suggestion}")
                lines.append("")
        elif llm_summary_enabled and not risks:
            lines.append("今日无明显风险。")
            lines.append("")
        else:
            lines.append("AI 总结功能已关闭，此处不生成内容。")
            lines.append("")

        # ===== 待办事项 =====
        lines.append("## 待办事项")
        lines.append("")
        if todos:
            for i, t in enumerate(todos, 1):
                title = t.get("title", "")
                chat = t.get("chat", "")
                person = t.get("person", "")
                priority = t.get("priority", "中")
                context = t.get("context", "")
                deadline = t.get("deadline", "")
                source = t.get("source", "")

                flag = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(priority, "🟡")
                source_tag = " [未回复]" if source == "unreplied" else ""

                lines.append(f"{i}. {flag} **{title}**{source_tag}")
                if person:
                    lines.append(f"   - 来源: {chat} ({person})")
                else:
                    lines.append(f"   - 来源: {chat}")
                if deadline:
                    lines.append(f"   - 截止: {deadline}")
                if context:
                    lines.append(f"   - 上下文: {context[:200]}")
                lines.append("")
        else:
            lines.append("暂无待办事项。")
            lines.append("")

        # ===== 自定义分析维度 (extra) =====
        extra = summary.get("extra", {})
        if llm_summary_enabled and isinstance(extra, dict) and extra:
            lines.append("## 自定义分析")
            lines.append("")
            for key, value in extra.items():
                # 可读化的 key
                display_key = key.replace("_", " ").title()
                lines.append(f"### {display_key}")
                lines.append("")
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            for k, v in item.items():
                                lines.append(f"- **{k}**: {v}")
                        else:
                            lines.append(f"- {item}")
                elif isinstance(value, dict):
                    for k, v in value.items():
                        lines.append(f"- **{k}**: {v}")
                else:
                    lines.append(str(value))
                lines.append("")

        content = "\n".join(lines)

        # 确保输出目录存在
        os.makedirs(self._output_dir, exist_ok=True)

        # 写入文件（文件名用单日期，跨天时取起始日期，保证周报能匹配到）
        ext = self._ext()
        date_for_file = date_str.split("~")[0][:10] if "~" in date_str else date_str
        filename = f"工作总结_{date_for_file}.{ext}"
        filepath = os.path.join(self._output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"工作总结已生成: {filepath}")
        return filepath

    def _write_chat_detail(self, analysis: Dict, date_str: str) -> str:
        """写入对话详情文件（原始消息时间线，纯聊天记录）"""
        my_chats = analysis.get("my_chats", [])
        unreplied = analysis.get("unreplied", [])

        lines = []
        lines.append(f"# 对话详情 - {date_str}")
        lines.append("")
        unreplied_count = analysis.get("unreplied_count", len(unreplied))
        detail_summary = (f"> 共 {analysis.get('total_messages', 0)} 条消息，"
                          f"你参与了 {len(my_chats)} 个会话，"
                          f"发言 {analysis.get('my_count', 0)} 条")
        if unreplied_count:
            detail_summary += f"，{unreplied_count} 条待回复"
        lines.append(detail_summary)
        lines.append("")

        for chat_info in my_chats:
            chat = chat_info.get("chat", "未知")
            all_msgs = chat_info.get("all_messages", [])

            lines.append(f"## {chat}")
            lines.append("")

            for msg in sorted(all_msgs, key=lambda m: m.get("time_sort", m.get("time", ""))):
                time_str = msg.get("time", "")
                sender = msg.get("sender", "未知")
                content = msg.get("content", "")
                my_name = self._config.get("wechat", {}).get("my_name", "")
                is_me = "**" if sender and my_name and my_name in str(sender) else ""

                lines.append(f"- [{time_str}] {is_me}{sender}{is_me}: {content}")
            lines.append("")

        # 未回复的提醒 — 输出完整对话上下文（与上面的 my_chats 格式一致）
        if unreplied:
            lines.append("## 未回复的消息")
            lines.append("")
            for item in unreplied:
                chat = item.get("chat", "")
                msg = item.get("message", {})
                context = item.get("context", "")
                source = item.get("source", "")
                source_tag = " [私聊]" if source == "private_chat" else ""

                lines.append(f"### {chat}{source_tag}")
                lines.append("")
                if context:
                    lines.append(context)
                else:
                    sender = msg.get("sender", "")
                    content = msg.get("content", "")
                    time_str = msg.get("time", "")
                    lines.append(f"- [{time_str}] {sender}: {content}")
                lines.append("")
                lines.append("---")
                lines.append("")
            lines.append("")

        content = "\n".join(lines)

        os.makedirs(self._output_dir, exist_ok=True)
        ext = self._ext()
        date_for_file = date_str.split("~")[0][:10] if "~" in date_str else date_str
        filename = f"对话详情_{date_for_file}.{ext}"
        filepath = os.path.join(self._output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"对话详情已生成: {filepath}")
        return filepath

    def generate(self, analysis: Dict, summary: Dict, date_str: str,
                 llm_summary_enabled: bool = True) -> str:
        """生成日报文件（两个 markdown 文件）

        Args:
            analysis: message_filter.analyze() 的输出
            summary: summarizer.summarize() 的输出
            date_str: 日期
            llm_summary_enabled: 是否开启了 AI 总结

        Returns:
            工作总结文件的路径
        """
        # 生成工作总结（感悟 + 风险 + 待办）
        work_path = self._write_work_summary(analysis, summary, date_str,
                                              llm_summary_enabled=llm_summary_enabled)

        # 生成对话详情（纯聊天记录）
        if self._include_raw:
            self._write_chat_detail(analysis, date_str)

        return work_path
