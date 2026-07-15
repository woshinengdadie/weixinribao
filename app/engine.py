"""
运行引擎 - 将 main.py 的核心逻辑封装为可调用函数
支持自定义时间范围、输出目录、进度回调
"""

import os
import sys
import json
import logging
import threading
from datetime import datetime
from typing import Optional, Callable, Dict

logger = logging.getLogger(__name__)

# 确保 src 目录在路径中
_src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)


def _import_modules():
    """延迟导入核心模块"""
    from wechat_reader import WeChatReader
    from message_filter import MessageFilter
    from summarizer import Summarizer
    from report_generator import ReportGenerator
    from weekly_report_generator import WeeklyReportGenerator
    from todos_sync import (save_todos_json, sync_todos_to_local_html)
    return (WeChatReader, MessageFilter, Summarizer,
            ReportGenerator, WeeklyReportGenerator,
            save_todos_json, sync_todos_to_local_html)


def run_daily(config: dict, date_from: str, date_to: str,
              run_type: str = "手动运行",
              progress: Optional[Callable] = None,
              stop_event: Optional[threading.Event] = None) -> Dict:
    """
    执行一次完整的日报生成流程

    Args:
        config: 配置字典
        date_from: 开始时间 "2026-06-24 09:00"
        date_to: 结束时间 "2026-06-24 17:30"
        run_type: "手动运行" 或 "自动运行"
        progress: 进度回调函数 progress(msg)
        stop_event: 停止事件，设为 set() 后会在步骤间中断

    Returns:
        结果字典 {success, filepath, message, stats}
    """
    def log(msg):
        if progress:
            progress(msg)
        logger.info(msg)

    def _stopped():
        return stop_event and stop_event.is_set()

    log("正在读取微信消息...")

    # 解析时间范围
    try:
        since = datetime.strptime(date_from, "%Y-%m-%d %H:%M")
        until = datetime.strptime(date_to, "%Y-%m-%d %H:%M")
    except ValueError:
        return {"success": False, "message": "日期格式错误"}

    # 创建带时间戳的输出目录
    now = datetime.now()
    folder_name = f"{run_type}_{now.strftime('%Y%m%d_%H%M%S')}"
    # 计算输出根目录：绝对路径直接用，相对路径基于项目根目录解析
    raw_dir = config.get("output", {}).get("dir", "./output")
    if os.path.isabs(raw_dir):
        base_output = raw_dir
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base_output = os.path.join(project_root, raw_dir)
    base_output = os.path.normpath(base_output)
    run_output_dir = os.path.join(base_output, folder_name)
    os.makedirs(run_output_dir, exist_ok=True)

    # 修改配置中的输出目录指向本次运行的子文件夹（深拷贝，避免污染调用方传入的 config）
    import copy
    run_config = copy.deepcopy(config)
    run_config.setdefault("output", {})
    run_config["output"]["dir"] = run_output_dir

    # 读取开关状态
    llm_summary_enabled = config.get("llm_summary_enabled", True)

    try:
        (WeChatReader_cls, MessageFilter_cls, Summarizer_cls,
         ReportGenerator_cls, _,
         save_todos_json_fn, sync_todos_to_local_html_fn) = _import_modules()

        # ===== 1. 读取消息 =====
        reader = WeChatReader_cls(run_config)

        # 先快速测试密钥是否有效
        log("正在测试密钥连接...")
        key_test = reader.test_keys()
        if not key_test.get("ok"):
            log(f"[密钥测试] ❌ {key_test.get('message', '未知错误')}")
            if key_test.get("error_detail"):
                log(f"[密钥测试] 详情: {key_test['error_detail']}")
            log("[密钥测试] 请打开「密钥配置」页面重新填写 passphrase 并配置密钥。")
            return {"success": False, "message": f"密钥无效: {key_test.get('message', '')}", "filepath": None}
        log(f"[密钥测试] {key_test.get('message', '')}")
        if key_test.get("sample_content"):
            log(f"[密钥测试] 样本: [{key_test.get('sample_chat', '')}] {key_test['sample_content']}")

        messages = reader.get_messages(since=since, progress=progress)
        log(f"共获取到 {len(messages)} 条消息")

        if not messages:
            log("⚠️ 没有获取到消息，开始诊断：")
            log("  1. 微信桌面版未运行或未登录")
            log("  2. 首次使用前未执行「初始化密钥」")
            log("  3. 微信昵称（my_name）与实际不一致")
            log("  4. 今天/指定时间范围内没有新的聊天记录")

            # 诊断密钥状态
            import json as _json
            import os as _os
            key_file = _os.path.expanduser("~/.wechat-cli/all_keys.json")
            config_file = _os.path.expanduser("~/.wechat-cli/config.json")
            if _os.path.exists(key_file):
                with open(key_file) as _f:
                    keys = _json.load(_f)
                    log(f"[诊断] all_keys.json 存在，包含 {len(keys)} 个密钥: {', '.join(list(keys.keys())[:5])}")
            else:
                log("[诊断] ~/.wechat-cli/all_keys.json 不存在！请先配置密钥")
            if _os.path.exists(config_file):
                with open(config_file) as _f:
                    cfg = _json.load(_f)
                    log(f"[诊断] config.json: db_dir={cfg.get('db_dir', 'N/A')}")
            else:
                log("[诊断] ~/.wechat-cli/config.json 不存在！请先配置密钥")

            return {"success": True, "message": "没有消息", "filepath": None}

        # 诊断：打印消息发送者样本，帮助排查 my_name 不匹配问题
        my_name = config.get("wechat", {}).get("my_name", "")
        senders = list({m.get("sender", "") for m in messages if m.get("sender")})
        log(f"[诊断] my_name='{my_name}'，消息发送者样本: {senders[:20]}")
        if my_name and my_name not in senders:
            log(f"⚠️ 所有消息发送者中未找到 'my_name'（{my_name}），可能导致参与度分析为空！请检查微信昵称配置。")

        # 检查点：步骤 1 完成后
        if _stopped():
            log("⏹ 已在步骤 1 后停止")
            return {"success": False, "message": "用户停止"}

        # ===== 2. 参与度分析 =====
        log("正在分析消息参与度...")
        filter_ = MessageFilter_cls(run_config)
        analysis = filter_.analyze(messages)
        my_chat_count = len(analysis.get('my_chats', []))
        unreplied_count = len(analysis.get('unreplied', []))
        log(f"你参与了 {my_chat_count} 个会话，发言 {analysis['my_count']} 条，未回复 {unreplied_count} 条")

        # 检查点：步骤 2 完成后
        if _stopped():
            log("⏹ 已在步骤 2 后停止")
            return {"success": False, "message": "用户停止"}

        # ===== 3. 构建待办事项 =====
        # 未回复待办：@我但未回复的消息，始终整理
        unreplied_todos = _build_unreplied_todos(analysis)

        summary = {"todos": [], "risk_points": [], "insights": ""}

        # 构造日期显示字符串（跨天则显示范围）
        since_date = since.strftime("%Y-%m-%d")
        until_date = until.strftime("%Y-%m-%d")
        if since_date == until_date:
            date_display = since_date
        else:
            date_display = f"{since_date}~{until_date}"

        if llm_summary_enabled:
            # ===== 开关打开：调用 LLM 总结 =====
            log("正在用 AI 生成工作总结...")
            # 将输出目录注入 analysis，供 summarizer debug dump 使用
            analysis["_output_dir"] = run_output_dir
            summarizer = Summarizer_cls(run_config)
            summary = summarizer.summarize(analysis, date_display)

            # 检测 LLM 调用是否失败（无返回或返回为空）
            llm_err = summary.pop("_llm_error", None)
            if llm_err:
                log(f"❌ AI 总结失败：{llm_err}")
                log("  已跳过 AI 总结，仅整理对话详情和未回复待办")

            # 合并 LLM 待办 + 未回复待办
            llm_todos = summary.get("todos", [])
            all_todos = _merge_todos(llm_todos, unreplied_todos)
            summary["todos"] = all_todos

            risk_count = len(summary.get("risk_points", []))
            todo_count = len(all_todos)
            log(f"已生成 {risk_count} 项风险识别，{todo_count} 项待办（含 {len(unreplied_todos)} 项未回复）")
        else:
            # ===== 开关关闭：只整理未回复待办 =====
            log("AI 总结已关闭，仅整理对话详情和未回复待办")
            summary["todos"] = unreplied_todos

        # 检查点：步骤 3 完成后
        if _stopped():
            log("⏹ 已在步骤 3 后停止")
            return {"success": False, "message": "用户停止"}

        # ===== 4. 生成日报文件 =====
        log("正在生成日报文件...")
        generator = ReportGenerator_cls(run_config)
        filepath = generator.generate(analysis, summary, date_display,
                                       llm_summary_enabled=llm_summary_enabled)
        log(f"文件已生成: {folder_name}/")

        # ===== 5. 保存待办 =====
        save_todos_json_fn(summary.get("todos", []), run_output_dir)
        sync_todos_to_local_html_fn(summary.get("todos", []), date_display, base_output)
        log("待办已更新")

        result = {
            "success": True,
            "filepath": filepath,
            "folder": folder_name,
            "stats": {
                "work_items": my_chat_count,
                "todos": len(summary.get("todos", [])),
                "risks": len(summary.get("risk_points", [])),
            }
        }
        return result

    except Exception as e:
        logger.exception("运行出错")
        log(f"运行失败: {e}")
        return {"success": False, "message": str(e)}


def _build_unreplied_todos(analysis: Dict) -> list:
    """将 @我但未回复 的消息整理为待办"""
    unreplied = analysis.get("unreplied", [])
    todos = []
    for item in unreplied:
        chat = item.get("chat", "")
        msg = item.get("message", {})
        content = msg.get("content", "")
        sender = msg.get("sender", "")
        time_str = msg.get("time", "")

        todos.append({
            "title": f"回复 {chat} 中 {sender} 的消息",
            "chat": chat,
            "person": sender,
            "context": f"[{time_str}] {content[:200]}",
            "priority": "高",
            "deadline": "",
            "source": "unreplied",
        })
    return todos


def _merge_todos(llm_todos: list, unreplied_todos: list) -> list:
    """合并 LLM 总结的待办 和 未回复待办，去重"""
    seen = set()
    merged = []

    for t in unreplied_todos:
        key = (t.get("title", ""), t.get("chat", ""))
        if key not in seen:
            seen.add(key)
            merged.append(t)

    for t in llm_todos:
        key = (t.get("title", ""), t.get("chat", ""))
        if key not in seen:
            seen.add(key)
            merged.append(t)

    return merged


def run_weekly(config: dict, date_str: Optional[str] = None,
               progress: Optional[Callable] = None) -> Dict:
    """生成周报"""
    def log(msg):
        if progress:
            progress(msg)
        logger.info(msg)

    log("正在生成周报...")

    try:
        (_, _, _, _, WeeklyReportGenerator_cls, _, _) = _import_modules()

        generator = WeeklyReportGenerator_cls(config)
        filepath = generator.generate(date_str, progress=progress)

        if filepath:
            log(f"周报已生成")
            return {"success": True, "filepath": filepath}
        else:
            log("周报生成失败：未找到足够的数据")
            return {"success": False, "message": "未找到足够的数据"}

    except Exception as e:
        logger.exception("周报生成出错")
        log(f"周报生成失败: {e}")
        return {"success": False, "message": str(e)}
