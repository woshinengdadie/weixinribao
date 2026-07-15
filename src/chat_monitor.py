"""
会话监控模块 - 定时捞取指定群聊消息，生成 .md 文件
支持手动选取 .md 文件发送给 LLM 进行分析

功能：
  1. 定时调度（间隔/定点）自动捞取群消息
  2. 保存为可读的 .md 文件（时间线格式）
  3. 手动选文件 + 写要求 → 发给 LLM 分析
"""

import os
import sys
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, List

# 确保 src 在路径中
_src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

logger = logging.getLogger(__name__)

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _get_project_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_output_base_dir(config: dict) -> str:
    """获取用户配置的输出根目录（绝对路径）"""
    raw_dir = config.get("output", {}).get("dir", "./output")
    if os.path.isabs(raw_dir):
        return os.path.normpath(raw_dir)
    return os.path.normpath(os.path.join(_get_project_root(), raw_dir))


class ChatMonitor:
    """会话监控引擎"""

    def __init__(self, config: dict):
        self._config = config
        self._monitor_cfg = config.get("chat_monitor", {})
        self._output_base = _get_output_base_dir(config)
        self._last_run_time: Optional[datetime] = None
        # 从配置文件读取上次运行时间
        self._load_last_run_time()

    # ---- 时间窗口 ----

    def _get_time_window(self) -> tuple:
        """返回 (since, until) 时间窗口"""
        until = datetime.now()
        if self._last_run_time:
            since = self._last_run_time
        else:
            # 首次运行：默认捞最近 N 小时
            hours = self._monitor_cfg.get("fetch", {}).get("first_run_hours", 24)
            since = until - timedelta(hours=hours)
        return since, until

    # ---- last_run_time 持久化 ----

    def _get_state_file(self) -> str:
        state_dir = os.path.join(_get_project_root(), "config")
        os.makedirs(state_dir, exist_ok=True)
        return os.path.join(state_dir, "chat_monitor_state.json")

    def _load_last_run_time(self):
        """从状态文件加载上次运行时间"""
        try:
            path = self._get_state_file()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ts = data.get("last_run_time")
                if ts:
                    self._last_run_time = datetime.fromisoformat(ts)
                    logger.info(f"[ChatMonitor] 上次运行时间: {self._last_run_time}")
        except Exception as e:
            logger.warning(f"[ChatMonitor] 加载状态失败: {e}")

    def _save_last_run_time(self):
        """保存上次运行时间"""
        try:
            now = datetime.now()
            path = self._get_state_file()
            data = {"last_run_time": now.isoformat()}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._last_run_time = now
        except Exception as e:
            logger.warning(f"[ChatMonitor] 保存状态失败: {e}")

    # ---- 消息获取 ----

    def _fetch_messages(self, chat_name: str, since: datetime,
                        progress: Optional[Callable] = None) -> List[Dict]:
        """调用 wechat-cli 获取指定群聊的消息"""
        try:
            from wechat_reader import WeChatReader, _extract_json as ext_json
        except ImportError:
            logger.error("[ChatMonitor] wechat_reader 模块导入失败")
            return []

        reader = WeChatReader(self._config)
        since_str = since.strftime("%Y-%m-%d %H:%M:%S")

        try:
            output = reader.run_wechat_cli(
                "history", chat_name,
                "--start-time", since_str,
                "--format", "json",
            )
        except Exception as e:
            logger.error(f"[ChatMonitor] [{chat_name}] wechat-cli 调用失败: {e}")
            if progress:
                progress(f"❌ [{chat_name}] 调用失败: {e}")
            return []

        if not output:
            logger.warning(f"[ChatMonitor] [{chat_name}] 无输出")
            return []

        extracted = ext_json(output)
        if not extracted:
            logger.warning(f"[ChatMonitor] [{chat_name}] JSON 提取失败")
            return []

        try:
            data = json.loads(extracted)
        except json.JSONDecodeError:
            logger.warning(f"[ChatMonitor] [{chat_name}] JSON 解析失败")
            return []

        if isinstance(data, dict):
            raw_msgs = data.get("messages", [])
            count = data.get("count", len(raw_msgs))
            logger.info(f"[ChatMonitor] [{chat_name}] 获取 {count} 条消息")
            if isinstance(raw_msgs, list):
                return [m if isinstance(m, dict) else {"content": str(m)}
                        for m in raw_msgs]
        return []

    # ---- .md 渲染 ----

    def _render_markdown(self, chat_name: str, messages: List[Dict],
                         since: datetime, until: datetime) -> str:
        """将消息列表渲染为可读的 .md 文件"""
        lines = []
        lines.append(f"# 群聊消息记录 - {chat_name}")
        lines.append("")
        lines.append(f"> 时间范围：{since.strftime('%Y-%m-%d %H:%M')} ~ {until.strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"> 消息条数：{len(messages)} 条")
        lines.append("")
        lines.append("---")
        lines.append("")

        for msg in messages:
            if isinstance(msg, str):
                content = msg
                sender = ""
                time_str = ""
            else:
                content = msg.get("content", msg.get("message", ""))
                sender = msg.get("sender", msg.get("display_name", ""))
                time_str = msg.get("time", msg.get("time_sort", ""))

            # 格式化时间
            if time_str and len(time_str) >= 5:
                # 截取 HH:MM 部分
                if " " in time_str:
                    time_part = time_str.split(" ")[-1][:5]
                else:
                    time_part = time_str[:5]
            else:
                time_part = "??:??"

            if sender:
                lines.append(f"## [{time_part}] {sender}")
            else:
                lines.append(f"## [{time_part}]")
            lines.append("")
            lines.append(str(content).strip() if content else "（空消息）")
            lines.append("")

        lines.append("---")
        lines.append(f"（共 {len(messages)} 条消息）")

        return "\n".join(lines)

    # ---- 文件保存 ----

    def _save_messages_md(self, chat_name: str, content: str,
                         since: datetime, until: datetime,
                         folder_path: Optional[str] = None) -> str:
        """保存消息 .md 文件到带时间戳的子目录

        Args:
            folder_path: 可选外部指定的输出目录，避免重复计算 timestamp 导致目录不一致
        """
        if folder_path is None:
            timestamp = until.strftime("%Y%m%d_%H%M%S")
            folder_path = os.path.join(self._output_base, f"会话监控_{timestamp}")
        os.makedirs(folder_path, exist_ok=True)

        # 文件名：群名_消息_时间.md（替换非法字符）
        safe_name = chat_name.replace("/", "_").replace("\\", "_").replace(":", "_")
        time_str = until.strftime("%Y-%m-%d_%H-%M")
        filename = f"{safe_name}_消息_{time_str}.md"
        filepath = os.path.join(folder_path, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath

    def _save_metadata(self, folder_path: str, since: datetime, until: datetime,
                       results: List[Dict]):
        """保存元数据 JSON"""
        meta = {
            "run_time": until.isoformat(),
            "time_window": {
                "since": since.isoformat(),
                "until": until.isoformat(),
            },
            "chats": results,
        }
        path = os.path.join(folder_path, "_元数据.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    # ---- 主运行流程 ----

    def run(self, progress: Optional[Callable] = None,
            stop_event: Optional[threading.Event] = None):
        """执行一次监控运行"""
        def log(msg):
            if progress:
                progress(msg)
            logger.info(msg)

        def _stopped():
            return stop_event and stop_event.is_set()

        chats = self._monitor_cfg.get("chats", [])
        if not chats:
            log("⚠️ 没有配置监控群聊")
            return

        since, until = self._get_time_window()
        log(f"开始会话监控（{len(chats)} 个群聊）")
        log(f"时间窗口: {since.strftime('%Y-%m-%d %H:%M')} ~ {until.strftime('%Y-%m-%d %H:%M')}")

        # 统一计算输出目录，避免循环内重复计算及跨秒不一致
        timestamp = until.strftime("%Y%m%d_%H%M%S")
        folder_path = os.path.join(self._output_base, f"会话监控_{timestamp}")
        os.makedirs(folder_path, exist_ok=True)

        results = []
        for i, chat_name in enumerate(chats):
            if _stopped():
                log(f"⏹ 已停止，已处理 {i}/{len(chats)} 个群聊")
                break

            log(f"[{i+1}/{len(chats)}] 正在获取 [{chat_name}] 的消息...")

            try:
                messages = self._fetch_messages(chat_name, since, progress)
            except Exception as e:
                log(f"❌ [{chat_name}] 获取失败: {e}")
                results.append({"chat": chat_name, "count": 0, "error": str(e)})
                continue

            if not messages:
                log(f"  [{chat_name}] 无消息")
                results.append({"chat": chat_name, "count": 0, "error": "无消息"})
                continue

            # 渲染并保存
            md_content = self._render_markdown(chat_name, messages, since, until)
            filepath = self._save_messages_md(chat_name, md_content, since, until, folder_path=folder_path)

            log(f"  ✅ [{chat_name}] {len(messages)} 条消息 → {os.path.basename(filepath)}")
            results.append({
                "chat": chat_name,
                "count": len(messages),
                "file": filepath,
                "error": None,
            })

        # 保存元数据
        if results:
            try:
                self._save_metadata(folder_path, since, until, results)
            except Exception as e:
                logger.warning(f"保存元数据失败: {e}")

        # 更新运行时间
        self._save_last_run_time()

        total_msgs = sum(r.get("count", 0) for r in results)
        log(f"会话监控完成: {len(results)} 个群聊，共 {total_msgs} 条消息")

    # ---- 手动 LLM 分析 ----

    def analyze_files(self, filepaths: List[str], requirement: str,
                      progress: Optional[Callable] = None) -> Dict:
        """读取选中的 .md 文件，发送给 LLM 分析

        Returns:
            {"success": bool, "reports": [{"chat": "...", "file": "...", "analysis": "..."}]}
        """
        def log(msg):
            if progress:
                progress(msg)
            logger.info(msg)

        if not filepaths:
            return {"success": False, "message": "请选择至少一个文件"}

        # 初始化 LLM 客户端
        llm_config = self._config.get("llm", {})
        use_local = llm_config.get("local_model", {}).get("enabled", False)

        client = None
        local_llm = None

        if use_local:
            try:
                from local_llm import LocalLLM
                lc = LocalLLM.get_instance(self._config)
                if lc and lc.is_ready():
                    local_llm = lc
                    log("使用本地模型分析")
                else:
                    err = lc.get_load_error() if lc else "None"
                    return {"success": False, "message": f"本地模型不可用: {err}"}
            except Exception as e:
                return {"success": False, "message": f"本地模型初始化失败: {e}"}
        else:
            try:
                from openai import OpenAI
                api_key = llm_config.get("api_key", "")
                base_url = llm_config.get("base_url", "")
                if not api_key or not base_url:
                    return {"success": False, "message": "LLM 未配置 API Key / Base URL"}
                client = OpenAI(api_key=api_key, base_url=base_url)
                log("使用远程 LLM 分析")
            except Exception as e:
                return {"success": False, "message": f"LLM 初始化失败: {e}"}

        # 输出目录
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_dir = os.path.join(self._output_base, f"会话监控_分析报告")
        os.makedirs(report_dir, exist_ok=True)

        reports = []
        try:
            for i, filepath in enumerate(filepaths):
                log(f"[{i+1}/{len(filepaths)}] 正在分析: {os.path.basename(filepath)}")

                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()

                    # 截断过长内容（避免超上下文）
                    max_chars = 12000
                    if len(content) > max_chars:
                        content = content[:max_chars] + "\n\n...(消息过长已截断)"
                        log(f"  [WARN] 文件过长，已截断到 {max_chars} 字符")

                    # 构建 prompt
                    if requirement:
                        user_prompt = f"""请分析以下群聊消息记录。

## 分析要求
{requirement}

## 消息记录
{content}

请以 JSON 格式返回分析结果：
{{"topics": ["话题1"],"decisions": ["决策1"],"participants":[{{"name":"人名","role":"角色","key_points":["要点"]}}],"todos":[{{"title":"待办","person":"负责人","priority":"高/中/低","deadline":""}}],"risks":[{{"risk":"风险","level":"高/中/低","suggestion":"建议"}}],"summary":"一段话总结"}}

只返回 JSON，不要其他文字。"""
                    else:
                        user_prompt = f"""请全面分析以下群聊消息记录。

## 消息记录
{content}

请以 JSON 格式返回：
{{"topics": ["话题1"],"decisions": ["决策1"],"participants":[{{"name":"人名","role":"角色","key_points":["要点"]}}],"todos":[{{"title":"待办","person":"负责人","priority":"高/中/低","deadline":""}}],"risks":[{{"risk":"风险","level":"高/中/低","suggestion":"建议"}}],"summary":"一段话总结"}}

只返回 JSON，不要其他文字。"""

                    # 调用 LLM
                    if local_llm:
                        response = local_llm.chat(
                            messages=[
                                {"role": "system", "content": "你是专业的会议记录和对话分析助手。"},
                                {"role": "user", "content": user_prompt},
                            ],
                            temperature=llm_config.get("temperature", 0.3),
                            max_tokens=min(llm_config.get("max_tokens", 4000), 4096),
                        )
                    else:
                        resp = client.chat.completions.create(
                            model=llm_config.get("model", ""),
                            messages=[
                                {"role": "system", "content": "你是专业的会议记录和对话分析助手。"},
                                {"role": "user", "content": user_prompt},
                            ],
                            temperature=llm_config.get("temperature", 0.3),
                            max_tokens=llm_config.get("max_tokens", 4000),
                            timeout=120,
                        )
                        response = resp.choices[0].message.content

                    if not response or not response.strip():
                        log(f"  [WARN] LLM 无返回")
                        reports.append({"file": filepath, "error": "LLM 无返回"})
                        continue

                    log(f"  [OK] 分析完成，{len(response)} 字符")

                    # 解析 JSON
                    from summarizer import _parse_llm_json
                    analysis = _parse_llm_json(response)

                    # 渲染 .md 报告
                    report_md = self._render_analysis_md(filepath, analysis, requirement)
                    safe_name = os.path.basename(filepath).replace("消息_", "分析_")
                    report_path = os.path.join(report_dir, safe_name)
                    with open(report_path, "w", encoding="utf-8") as f:
                        f.write(report_md)

                    log(f"  [OK] 报告已保存: {os.path.basename(report_path)}")
                    reports.append({
                        "file": filepath,
                        "report": report_path,
                        "error": None,
                    })

                except Exception as e:
                    log(f"  [FAILED] 分析失败: {e}")
                    reports.append({"file": filepath, "error": str(e)})
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

        return {"success": True, "reports": reports}

    def _render_analysis_md(self, source_file: str, analysis: Optional[Dict],
                            requirement: str) -> str:
        """渲染分析报告 .md"""
        lines = []
        chat_name = os.path.basename(source_file).replace("_消息_", "_").replace(".md", "")
        lines.append(f"# 会话分析报告 - {chat_name}")
        lines.append("")
        lines.append(f"> 分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        if requirement:
            lines.append(f"> 分析要求: {requirement}")
        lines.append(f"> 数据来源: {os.path.basename(source_file)}")
        lines.append("")

        if analysis:
            topics = analysis.get("topics", [])
            if topics:
                lines.append("## 📌 核心话题")
                lines.append("")
                for t in topics:
                    lines.append(f"- {t}")
                lines.append("")

            decisions = analysis.get("decisions", [])
            if decisions:
                lines.append("## 📋 做出的决策")
                lines.append("")
                for d in decisions:
                    lines.append(f"- {d}")
                lines.append("")

            participants = analysis.get("participants", [])
            if participants:
                lines.append("## 👥 成员发言要点")
                lines.append("")
                for p in participants:
                    if isinstance(p, str):
                        lines.append(f"- {p}")
                    else:
                        name = p.get("name", "未知")
                        role = p.get("role", "")
                        key_points = p.get("key_points", [])
                        lines.append(f"- **{name}** ({role})")
                        for kp in key_points:
                            lines.append(f"  - {kp}")
                lines.append("")

            todos = analysis.get("todos", [])
            if todos:
                lines.append("## ✅ 待办事项")
                lines.append("")
                for t in todos:
                    if isinstance(t, str):
                        lines.append(f"- {t}")
                    else:
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

            risks = analysis.get("risks", [])
            if risks:
                lines.append("## ⚠️ 风险与问题")
                lines.append("")
                for r in risks:
                    if isinstance(r, str):
                        lines.append(f"- {r}")
                    else:
                        r_text = r.get("risk", "")
                        level = r.get("level", "中")
                        suggestion = r.get("suggestion", "")
                        flag = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(level, "🟡")
                        lines.append(f"- {flag} **[{level}风险]** {r_text}")
                        if suggestion:
                            lines.append(f"  - 建议: {suggestion}")
                lines.append("")

            summary = analysis.get("summary", "")
            if summary:
                lines.append("## 📝 总结")
                lines.append("")
                lines.append(summary)
                lines.append("")
        else:
            lines.append("⚠️ AI 分析失败，无法解析返回结果")
            lines.append("")

        lines.append("---")
        return "\n".join(lines)

    # ---- 文件列表 ----

    @staticmethod
    def list_monitor_files(output_dir: str) -> List[Dict]:
        """列出所有会话监控生成的 .md 文件"""
        import glob
        results = []
        pattern = os.path.join(output_dir, "会话监控_*", "*.md")
        for filepath in sorted(glob.glob(pattern), reverse=True):
            # 跳过分析报告目录中的文件
            if "会话监控_分析报告" in filepath:
                continue
            # 跳过元数据
            if os.path.basename(filepath).startswith("_"):
                continue
            results.append({
                "path": filepath,
                "name": os.path.basename(filepath),
                "folder": os.path.basename(os.path.dirname(filepath)),
                "size": os.path.getsize(filepath),
                "mtime": datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d %H:%M"),
            })
        return results


class ChatMonitorScheduler:
    """会话监控调度器（复用 DailyScheduler 的设计模式）"""

    def __init__(self, config_provider: Callable, run_callback: Callable):
        self._config_provider = config_provider
        self._run_callback = run_callback
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_run_time: Optional[datetime] = None
        self._next_run_time: Optional[datetime] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._update_next_run()
        logger.info(f"[ChatMonitorScheduler] 已启动，下次运行: {self._next_run_time}")

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        logger.info("[ChatMonitorScheduler] 已停止")

    def trigger_now(self):
        t = threading.Thread(target=self._run_callback, daemon=True)
        t.start()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def next_run(self) -> Optional[str]:
        return self._next_run_time.strftime("%Y-%m-%d %H:%M") if self._next_run_time else None

    @property
    def last_run(self) -> Optional[str]:
        return self._last_run_time.strftime("%Y-%m-%d %H:%M") if self._last_run_time else None

    def _update_next_run(self):
        config = self._config_provider()
        mon = config.get("chat_monitor", {})
        sched = mon.get("schedule", {})
        mode = sched.get("mode", "interval")
        now = datetime.now()

        if mode == "interval":
            interval = sched.get("interval_seconds", 3600)
            if self._last_run_time:
                self._next_run_time = self._last_run_time + timedelta(seconds=interval)
            else:
                self._next_run_time = now + timedelta(seconds=interval)
        else:
            time_str = sched.get("scheduled_time", "17:30")
            try:
                hour, minute = map(int, time_str.split(":"))
                next_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_dt <= now:
                    next_dt += timedelta(days=1)
                self._next_run_time = next_dt
            except (ValueError, TypeError):
                self._next_run_time = None

    def _loop(self):
        while not self._stop_event.is_set():
            config = self._config_provider()
            mon = config.get("chat_monitor", {})
            sched = mon.get("schedule", {})
            mode = sched.get("mode", "interval")
            now = datetime.now()

            should_run = False

            if mode == "interval":
                interval = sched.get("interval_seconds", 3600)
                if self._last_run_time is None or (now - self._last_run_time).total_seconds() >= interval:
                    should_run = True
            else:
                time_str = sched.get("scheduled_time", "17:30")
                try:
                    hour, minute = map(int, time_str.split(":"))
                    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if self._last_run_time is None or self._last_run_time.date() < now.date():
                        if target <= now <= target + timedelta(minutes=5):
                            should_run = True
                except (ValueError, TypeError):
                    should_run = False

            if should_run:
                self._update_next_run()
                logger.info(f"[ChatMonitorScheduler] 触发监控: {now.isoformat()}")
                try:
                    self._run_callback()
                    self._last_run_time = now  # 成功后更新，失败保留以便重试
                except Exception as e:
                    logger.error(f"[ChatMonitorScheduler] 执行失败: {e}")

            if self._next_run_time is None:
                self._update_next_run()

            time.sleep(15)
