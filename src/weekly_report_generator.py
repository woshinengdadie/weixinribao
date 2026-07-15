"""
周报生成模块 - 汇总本周每日日报，AI 提炼为周报
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class WeeklyReportGenerator:
    """周报生成器"""

    def __init__(self, config: dict):
        self._config = config
        self._output_dir = config.get("output", {}).get("dir", "./output")
        self._llm_config = config.get("llm", {})
        self._llm_enabled = self._llm_config.get("enabled", False)
        self._client = None
        if self._llm_enabled:
            self._init_llm(self._llm_config)

    def _init_llm(self, llm_config: dict):
        """初始化 LLM 客户端"""
        try:
            from openai import OpenAI
            api_key = llm_config.get("api_key", "")
            base_url = llm_config.get("base_url", "")
            if not api_key or not base_url:
                self._llm_enabled = False
                return
            self._client = OpenAI(api_key=api_key, base_url=base_url)
        except Exception as e:
            logger.error(f"LLM 初始化失败: {e}")
            self._llm_enabled = False

    def get_week_range(self, date_str: Optional[str] = None) -> tuple:
        """获取指定日期所在周的周一~周日日期范围

        Returns:
            (monday_date, sunday_date, week_label)
        """
        if date_str:
            today = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            today = datetime.now()

        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        week_num = monday.isocalendar()[1]
        week_label = f"{monday.year}-W{week_num:02d}"

        return monday, sunday, week_label

    def find_daily_reports(self, monday: datetime, sunday: datetime) -> List[Dict]:
        """扫描 output 目录，找出本周范围内所有的日报文件

        同时在顶层目录和子目录（手动运行_*/自动运行_*/）中查找。

        Returns:
            [{"date": "2026-06-22", "weekday": "周一", "content": "..."}, ...]
        """
        reports = []

        current = monday
        while current <= sunday:
            date_str = current.strftime("%Y-%m-%d")
            weekday = WEEKDAY_NAMES[current.weekday()]

            # 查找工作总结文件（顶层 + 子目录）
            filename = f"工作总结_{date_str}.md"
            candidate_paths = [
                os.path.join(self._output_dir, filename),                                    # 顶层
                os.path.join(self._output_dir, "**", filename),                              # 所有子目录
            ]

            found = None
            for pattern in candidate_paths:
                import glob
                matches = glob.glob(pattern, recursive=True)
                if matches:
                    # 选最新修改的那个
                    matches.sort(key=os.path.getmtime, reverse=True)
                    found = matches[0]
                    break

            if found:
                try:
                    with open(found, "r", encoding="utf-8") as f:
                        content = f.read()
                    reports.append({
                        "date": date_str,
                        "weekday": weekday,
                        "content": content,
                        "source": found,
                    })
                    logger.info(f"找到日报 {date_str}: {found}")
                except Exception as e:
                    logger.warning(f"读取日报失败 {found}: {e}")
            else:
                logger.debug(f"未找到 {date_str} 的日报文件")

            current += timedelta(days=1)

        return reports

    def _extract_sections(self, content: str) -> Dict:
        """从工作总结 markdown 中提取关键段落"""
        sections = {"summary": "", "todos": "", "risks": ""}

        current_section = None
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("## 今日摘要"):
                current_section = "summary"
            elif line.startswith("## 待办事项"):
                current_section = "todos"
            elif line.startswith("## 风险识别"):
                current_section = "risks"
            elif line.startswith("## "):
                current_section = None
            elif current_section and line:
                sections[current_section] += line + "\n"

        return sections

    def _build_llm_prompt(self, reports: List[Dict]) -> str:
        """构建 LLM 提示词，让 AI 整合多天工作总结为周报"""
        daily_text = []
        for r in reports:
            sections = self._extract_sections(r["content"])
            daily_text.append(
                f"### {r['date']} {r['weekday']}\n"
                f"总结: {sections['summary'][:300]}\n"
                f"待办: {sections['todos'][:200]}\n"
            )

        prompt = f"""你是我的工作周报助手。请基于以下本周每日工作总结，生成周报。

## 本周工作总结
{chr(10).join(daily_text)}

## 要求
请以 JSON 格式返回：
1. weekly_summary: 本周工作总体概述（100字以内）
2. key_achievements: 本周关键成果（数组，每条50字以内）
3. next_week_plan: 下周计划（数组，每条30字以内）
4. risks_and_blockers: 风险和阻碍（数组）

只返回 JSON，不要其他文字。"""

        return prompt

    def _generate_with_llm(self, reports: List[Dict]) -> Dict:
        """使用 LLM 生成周报内容"""
        if not self._client:
            result = self._generate_with_rules(reports)
            result["_llm_error"] = "LLM 客户端未初始化"
            return result

        try:
            model = self._llm_config.get("model", "")
            temperature = self._llm_config.get("temperature", 0.3)
            max_tokens = self._llm_config.get("max_tokens", 1500)
            prompt = self._build_llm_prompt(reports)

            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是一个专业的工作周报助手。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=90,  # 90 秒超时
            )

            content = response.choices[0].message.content
            if not content or not content.strip():
                logger.warning("[Weekly] LLM 返回消息为空")
                result = self._generate_with_rules(reports)
                result["_llm_error"] = "LLM 返回消息为空，请检查 API Key 和服务状态"
                return result

            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                return json.loads(content[json_start:json_end])
            return json.loads(content)
        except Exception as e:
            logger.error(f"LLM 周报生成失败: {e}")
            result = self._generate_with_rules(reports)
            result["_llm_error"] = f"LLM API 调用失败: {e}"
            return result

    def _generate_with_rules(self, reports: List[Dict]) -> Dict:
        """规则模式：简单拼接各天摘要"""
        all_items = []
        for r in reports:
            sections = self._extract_sections(r["content"])
            if sections["summary"].strip():
                all_items.append({
                    "date": r["date"],
                    "weekday": r["weekday"],
                    "summary": sections["summary"].strip()[:100],
                })

        return {
            "weekly_summary": f"本周共 {len(reports)} 个工作日有工作记录",
            "key_achievements": [f"{i['weekday']}: {i['summary'][:50]}" for i in all_items[:7]],
            "next_week_plan": ["继续推进本周未完成的工作"],
            "risks_and_blockers": [],
        }

    def _render_markdown(self, reports: List[Dict], llm_result: Dict,
                         week_label: str, monday: datetime, sunday: datetime) -> str:
        """渲染周报 markdown"""
        lines = []
        lines.append(f"# 工作周报 - {week_label}")
        lines.append(f"")
        lines.append(f"> {monday.strftime('%Y-%m-%d')}（周一）~ {sunday.strftime('%Y-%m-%d')}（周日）")
        lines.append("")

        # 总体概述
        lines.append("## 本周工作概述")
        lines.append("")
        lines.append(llm_result.get("weekly_summary", ""))
        lines.append("")

        # 关键成果
        achievements = llm_result.get("key_achievements", [])
        if achievements:
            lines.append("## 关键成果")
            lines.append("")
            for i, a in enumerate(achievements, 1):
                lines.append(f"{i}. {a}")
            lines.append("")

        # 每日详情
        lines.append("## 每日工作总结")
        lines.append("")
        for r in reports:
            sections = self._extract_sections(r["content"])
            lines.append(f"### {r['date']} {r['weekday']}")
            lines.append("")
            if sections["summary"].strip():
                lines.append(sections["summary"].strip())
            else:
                lines.append("（无工作记录）")
            lines.append("")

        # 下周计划
        plans = llm_result.get("next_week_plan", [])
        if plans:
            lines.append("## 下周计划")
            lines.append("")
            for i, p in enumerate(plans, 1):
                lines.append(f"{i}. {p}")
            lines.append("")

        # 风险
        risks = llm_result.get("risks_and_blockers", [])
        if risks:
            lines.append("## 风险与阻碍")
            lines.append("")
            for r in risks:
                lines.append(f"- {r}")
            lines.append("")

        return "\n".join(lines)

    def generate(self, date_str: Optional[str] = None,
                 progress: Optional[Callable] = None) -> Optional[str]:
        """生成周报

        Args:
            date_str: 可选，指定日期（获取该日期所在周的周报），默认本周
            progress: 进度回调函数，用于在前端日志中显示进度和错误信息

        Returns:
            周报文件的绝对路径，失败返回 None
        """
        def log(msg):
            if progress:
                progress(msg)
            logger.info(msg)

        monday, sunday, week_label = self.get_week_range(date_str)

        # 查找日报文件
        reports = self.find_daily_reports(monday, sunday)

        if not reports:
            logger.warning(f"未找到 {week_label} 的日报文件")
            return None

        # 生成周报内容
        if self._llm_enabled and self._client:
            llm_result = self._generate_with_llm(reports)
            if "_llm_error" in llm_result:
                err_msg = llm_result.pop("_llm_error")
                log(f"❌ 周报 AI 总结失败：{err_msg}，已使用规则模式替代")
        else:
            llm_result = self._generate_with_rules(reports)

        # 渲染
        content = self._render_markdown(reports, llm_result, week_label, monday, sunday)

        # 写入文件
        os.makedirs(self._output_dir, exist_ok=True)
        ext = self._config.get("output", {}).get("format", "md")
        if ext not in ("md", "txt"):
            ext = "md"
        filename = f"工作周报_{week_label}.{ext}"
        filepath = os.path.join(self._output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"周报已生成: {filepath}")
        return filepath
