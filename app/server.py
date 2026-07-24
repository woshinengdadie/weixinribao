"""
Flask 后端服务 - 提供 API 给前端 UI
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import yaml
import logging
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, List
from flask import Flask, request, jsonify, Response, send_from_directory

# 项目根目录（兼容 PyInstaller 打包和开发环境）
if getattr(sys, "frozen", False):
    # PyInstaller 打包：config/ 在 exe 同级目录
    PROJECT_ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "config.yaml")
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
APP_DIR = os.path.join(PROJECT_ROOT, "app")

# 确保 src 和 app 目录在路径中
for p in [SRC_DIR, PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

from app.log_handler import LogQueue, ProgressWriter, LogQueueHandler
from app.scheduler import DailyScheduler

# 将 logger 消息自动推送到前端日志面板
LogQueueHandler.attach(level=logging.INFO,
                       fmt="[%(levelname)s] %(name)s: %(message)s")

logger = logging.getLogger(__name__)

# Flask app — 静态文件目录在打包环境中需绝对路径
_static_folder = os.path.join(PROJECT_ROOT, "app", "static")
if not os.path.isdir(_static_folder):
    _static_folder = os.path.join(os.path.dirname(__file__), "static")
app = Flask(__name__, static_folder=_static_folder, static_url_path="")

# ======== 本地 API 鉴权 ========
import secrets as _secrets
import hashlib as _hashlib
_API_TOKEN = _secrets.token_hex(32)  # 每次启动生成随机 token，仅本进程可用


@app.before_request
def _check_auth():
    """对所有 /api/* 请求校验本地 token（SSE/static 除外）"""
    if not request.path.startswith("/api/"):
        return None
    if request.path == "/api/log/stream":
        # SSE EventSource 不支持自定义 header，token 通过 URL 参数 ?token=xxx 携带
        token = request.args.get("token", "")
        if not token or not _cmp_token(token):
            return jsonify({"success": False, "message": "forbidden"}), 403
        return None
    token = request.headers.get("X-Auth-Token", "")
    if not token or not _cmp_token(token):
        return jsonify({"success": False, "message": "forbidden"}), 403


def _cmp_token(token: str) -> bool:
    """常数时间比较防止时序攻击"""
    return _hashlib.compare_digest(token, _API_TOKEN)

# 全局状态（线程安全）
_log_queue = LogQueue.get()
_scheduler_lock = threading.Lock()
_scheduler: DailyScheduler = None
_current_stats = {"work_items": 0, "todos": 0, "risks": 0}
_last_run_time: str = "从未运行"

# 日报生成互斥锁：防止手动运行与自动运行同时执行（DBCache 非线程安全 +
# 同时间戳输出目录可能冲突 + all_todos.json 并发写入损坏）
_daily_run_lock = threading.Lock()

# 最新会话分析报告路径（供前端查询，替代从 SSE 日志正则提取的脆弱方案）
_last_analyze_report: str = ""

# 手动运行 & 会话分析的停止控制
_manual_stop_event: Optional[threading.Event] = None
_analyze_stop_event: Optional[threading.Event] = None
_manual_running = False
_analyze_running = False


# 配置缓存（避免频繁读盘，仅当 config.yaml mtime 变化时重新读取）
_config_cache: dict = {}
_config_cache_mtime: float = 0.0
_config_cache_lock = threading.Lock()


def _load_config() -> dict:
    """加载配置，支持环境变量覆盖敏感字段

    使用内存缓存 + 文件 mtime 失效机制，避免高频调用时频繁读盘。
    每次调用检查 config.yaml 的 mtime，变化时才重新读取并解析。
    环境变量覆盖在每次调用时实时应用（不缓存，因为环境变量不会在运行中变化）。
    """
    global _config_cache, _config_cache_mtime

    try:
        current_mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        current_mtime = 0.0

    with _config_cache_lock:
        if current_mtime != _config_cache_mtime or not _config_cache:
            # mtime 变化或首次调用，重新读取
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    _config_cache = yaml.safe_load(f) or {}
            except FileNotFoundError:
                # 如果 config.yaml 不存在，尝试从模板复制一份
                example_path = os.path.join(os.path.dirname(CONFIG_PATH), "config.yaml.example")
                # 兜底：PyInstaller --add-data 把 config 放到了 _internal\config\，
                # 如果顶层没有 example，去 _internal 里找
                if not os.path.exists(example_path) and getattr(sys, "frozen", False):
                    internal_example = os.path.join(PROJECT_ROOT, "_internal", "config", "config.yaml.example")
                    if os.path.exists(internal_example):
                        import shutil
                        shutil.copy(internal_example, example_path)
                        logger.info("已从 _internal 还原 config.yaml.example")
                if os.path.exists(example_path):
                    import shutil
                    shutil.copy(example_path, CONFIG_PATH)
                    logger.info("已从 config.yaml.example 创建 config.yaml，请修改配置后使用")
                    try:
                        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                            _config_cache = yaml.safe_load(f) or {}
                    except Exception:
                        _config_cache = {}
                else:
                    _config_cache = {}
            except Exception:
                _config_cache = {}
            # 如果文件是新创建的（current_mtime 为 0），重新获取真实 mtime
            if current_mtime == 0.0:
                try:
                    _config_cache_mtime = os.path.getmtime(CONFIG_PATH)
                except OSError:
                    _config_cache_mtime = 0.0
            else:
                _config_cache_mtime = current_mtime

    # 复制一份，避免环境变量覆盖污染缓存
    config = copy.deepcopy(_config_cache)

    # 环境变量覆盖（更安全）
    env_api_key = os.environ.get("WECHAT_AGENT_API_KEY", "")
    if env_api_key:
        config.setdefault("llm", {})
        config["llm"]["api_key"] = env_api_key
        config["llm"]["enabled"] = True

    env_my_name = os.environ.get("WECHAT_AGENT_MY_NAME", "")
    if env_my_name:
        config.setdefault("wechat", {})
        config["wechat"]["my_name"] = env_my_name

    return config


def _save_config(config: dict):
    """保存配置（原子写入：先写临时文件，再替换，避免断电/崩溃导致配置损坏）"""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    os.replace(tmp_path, CONFIG_PATH)
    # 写入后更新 mtime 缓存，使下次 _load_config 命中缓存
    global _config_cache, _config_cache_mtime
    with _config_cache_lock:
        _config_cache = dict(config)
        try:
            _config_cache_mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            _config_cache_mtime = 0.0


def _get_output_dir() -> str:
    """获取用户配置的输出目录（绝对路径）"""
    config = _load_config()
    output_dir = config.get("output", {}).get("dir", "./output")
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(PROJECT_ROOT, output_dir)
    return os.path.normpath(output_dir)


def _mask_api_key(key: str) -> str:
    """对 API Key 进行脱敏显示（只保留前后各4位）"""
    if not key or len(key) <= 8:
        return key
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def _get_weekly_reports():
    """获取所有周报文件列表"""
    output_dir = _get_output_dir()
    pattern = re.compile(r"工作周报_(.*)\.md$")
    reports = []
    if os.path.exists(output_dir):
        for fname in sorted(os.listdir(output_dir)):
            m = pattern.match(fname)
            if m:
                reports.append({
                    "name": m.group(1),
                    "path": os.path.join(output_dir, fname),
                })
    return reports


def _get_output_folders():
    """获取所有运行输出文件夹（同时检查默认 output/ 和用户配置的输出目录）"""
    sources = [
        os.path.join(PROJECT_ROOT, "output"),
        _get_output_dir(),
    ]
    pattern = re.compile(r"(手动运行|自动运行)_(\d{8}_\d{6})$")
    folders = []
    seen = set()
    for src in sources:
        if not os.path.exists(src):
            continue
        for name in sorted(os.listdir(src)):
            m = pattern.match(name)
            if m and os.path.isdir(os.path.join(src, name)):
                key = m.group(2)
                if key not in seen:
                    seen.add(key)
                    folders.append({
                        "name": name,
                        "type": m.group(1),
                        "timestamp": key,
                        "path": os.path.join(src, name),
                    })
    return sorted(folders, key=lambda x: x["timestamp"])


def _progress_sink(msg: str):
    """进度回调：写入日志队列"""
    _log_queue.write(msg)


def _run_daily_task(run_type: str = "自动运行"):
    """执行日报生成（由手动或调度器触发）

    使用 _daily_run_lock 互斥，防止手动运行与自动运行同时执行。
    若锁已被占用（另一类型运行中），本次跳过并记录日志。
    """
    global _last_run_time, _current_stats

    if not _daily_run_lock.acquire(blocking=False):
        _progress_sink(f"⏭ {run_type}已跳过：另一个日报生成任务正在运行")
        return

    try:
        config = _load_config()
        now = datetime.now()
        today_start = now.strftime("%Y-%m-%d") + " 00:00"
        today_end = now.strftime("%Y-%m-%d") + f" {now.strftime('%H:%M')}"

        _progress_sink(f"开始{run_type}...")

        from app.engine import run_daily
        result = run_daily(
            config=config,
            date_from=today_start,
            date_to=today_end,
            run_type=run_type,
            progress=_progress_sink,
        )

        if result.get("success"):
            _last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            stats = result.get("stats", {})
            _current_stats = stats
            _progress_sink(f"运行完成")
            # 自动运行完成后检查是否需要自动生成周报
            _maybe_auto_weekly(config)
        else:
            _progress_sink(f"运行失败: {result.get('message', '未知错误')}")
    finally:
        _daily_run_lock.release()


def _maybe_auto_weekly(config: dict):
    """自动运行成功后，若开启 weekly_auto 且今天是周五，则自动生成周报"""
    try:
        if not config.get("weekly_auto", False):
            return
        # 周五（weekday()==4）才自动生成
        if datetime.now().weekday() != 4:
            return
        _progress_sink("检测到今日周五且已开启自动周报，开始生成...")
        from app.engine import run_weekly
        result = run_weekly(config, None, progress=_progress_sink)
        if result.get("success"):
            _progress_sink("自动周报生成完成 ✅")
        else:
            _progress_sink(f"自动周报生成失败: {result.get('message', '')}")
    except Exception as e:
        logger.warning(f"自动周报生成异常: {e}")


# ========== API 路由 ==========

@app.route("/")
def index():
    html_path = os.path.join(app.static_folder, "index.html")
    if os.path.exists(html_path):
        with open(html_path, encoding="utf-8") as f:
            content = f.read()
    else:
        content = "<!doctype html><html><body>UI 文件缺失</body></html>"
    # 注入 token 和 SSE URL（含 token），确保前端 API 调用可鉴权
    sse_url = f"/api/log/stream?token={_API_TOKEN}"
    inject = f'<script>window.__APP_TOKEN__="{_API_TOKEN}";window.__SSE_URL__="{sse_url}";</script>'
    content = content.replace("</head>", inject + "\n</head>")
    return Response(content, mimetype="text/html; charset=utf-8")



# --- 配置 ---

@app.route("/api/config", methods=["GET"])
def api_get_config():
    config = _load_config()
    # 补充调度配置（从 config 中提取）
    schedule = config.get("schedule", {})

    # 检测密钥是否已配置（读取 ~/.wechat-cli/all_keys.json）
    keys_configured = False
    keys_count = 0
    try:
        keys_path = os.path.expanduser("~/.wechat-cli/all_keys.json")
        if os.path.exists(keys_path):
            with open(keys_path, "r", encoding="utf-8") as f:
                keys_data = json.load(f)
            keys_count = len(keys_data)
            keys_configured = keys_count > 0
    except Exception:
        pass

    return jsonify({
        "my_name": config.get("wechat", {}).get("my_name", ""),
        "wxid": config.get("wechat", {}).get("wxid", ""),
        "api_key": _mask_api_key(config.get("llm", {}).get("api_key", "")),
        "base_url": config.get("llm", {}).get("base_url", "https://token-plan-cn.xiaomimimo.com/v1"),
        "model": config.get("llm", {}).get("model", "mimo-v2.5-pro"),
        "temperature": config.get("llm", {}).get("temperature", 0.5),
        "max_tokens": config.get("llm", {}).get("max_tokens", 16000),
        "use_local_llm": config.get("llm", {}).get("local_model", {}).get("enabled", False),
        "local_model_name": config.get("llm", {}).get("local_model", {}).get("name", "qwen2.5-0.5b"),
        "db_dir": config.get("wechat", {}).get("db_dir", ""),
        "output_dir": config.get("output", {}).get("dir", "./output"),
        "output_format": config.get("output", {}).get("format", "md"),
        "exclude_chats": config.get("monitor", {}).get("exclude_chats", []),
        "important_contacts": config.get("monitor", {}).get("important_contacts", []),
        "rule_prompt": config.get("rule_prompt", ""),
        "llm_summary_enabled": config.get("llm_summary_enabled", True),
        "schedule_mode": schedule.get("mode", "scheduled"),
        "schedule_time": schedule.get("time", "17:30"),
        "schedule_daily_once": schedule.get("daily_once", True),
        "schedule_interval": schedule.get("interval_seconds", 1800),
        "weekly_auto": config.get("weekly_auto", False),
        "keys_configured": keys_configured,
        "keys_count": keys_count,
    })


@app.route("/api/config", methods=["POST"])
def api_save_config():
    data = request.json
    config = _load_config()

    config.setdefault("wechat", {})
    config["wechat"]["my_name"] = data.get("my_name", "")
    config["wechat"]["wxid"] = data.get("wxid", "")
    config["wechat"]["db_dir"] = data.get("db_dir", "")

    config.setdefault("llm", {})
    # 防止脱敏 key 覆盖真实 key：如果前端传来的 key 是脱敏版本，保留原值
    new_api_key = data.get("api_key", "")
    old_api_key = config.get("llm", {}).get("api_key", "")
    if new_api_key and old_api_key and _mask_api_key(old_api_key) == new_api_key:
        # 前端传回的是脱敏后的值，保留原始 key
        pass
    else:
        config["llm"]["api_key"] = new_api_key
    config["llm"]["base_url"] = data.get("base_url", "https://token-plan-cn.xiaomimimo.com/v1")
    config["llm"]["model"] = data.get("model", "mimo-v2.5-pro")
    config["llm"]["temperature"] = float(data.get("temperature", 0.5))
    config["llm"]["max_tokens"] = int(data.get("max_tokens", 16000))
    config["llm"]["enabled"] = True

    # 本地模型配置
    config.setdefault("llm", {}).setdefault("local_model", {})
    config["llm"]["local_model"]["enabled"] = data.get("use_local_llm", False)
    config["llm"]["local_model"]["name"] = data.get("local_model_name", "qwen2.5-0.5b")

    config.setdefault("output", {})
    config["output"]["dir"] = data.get("output_dir", "./output")
    config["output"]["format"] = data.get("output_format", "md")

    config.setdefault("monitor", {})
    config["monitor"]["exclude_chats"] = data.get("exclude_chats", [])
    config["monitor"]["important_contacts"] = data.get("important_contacts", [])

    config["rule_prompt"] = data.get("rule_prompt", "")

    config.setdefault("schedule", {})
    config["schedule"]["mode"] = data.get("schedule_mode", "scheduled")
    config["schedule"]["time"] = data.get("schedule_time", "17:30")
    config["schedule"]["daily_once"] = data.get("schedule_daily_once", True)
    config["schedule"]["interval_seconds"] = data.get("schedule_interval", 1800)

    config["weekly_auto"] = data.get("weekly_auto", False)

    config["llm_summary_enabled"] = data.get("llm_summary_enabled", True)

    _save_config(config)

    return jsonify({"success": True, "message": "配置已保存"})


@app.route("/api/config/rule", methods=["POST"])
def api_save_rule():
    """仅更新 AI 规则逻辑（rule_prompt），不影响其他配置项

    供「规则逻辑」Tab 独立保存，避免与基本设置耦合。
    """
    data = request.json or {}
    config = _load_config()
    config["rule_prompt"] = data.get("rule_prompt", "")
    _save_config(config)
    return jsonify({"success": True, "message": "规则已保存，下次运行生效"})


# --- 激活 ---

def _get_license_client():
    """获取 LicenseClient 实例，确保能找到 license_client 包"""
    import sys
    import os
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    from license_client import LicenseClient
    return LicenseClient(
        server_url="https://43.143.121.172",
        product_id="your_app_v1",
        app_name="WeChatWorkAgent",
        verify_ssl=False,
    )


@app.route("/api/activate", methods=["GET"])
def api_activate_info():
    """获取在线激活状态"""
    try:
        lc = _get_license_client()
        result = lc.check()
        if result["valid"]:
            expire_ts = int(result.get("expire_at", 0))
            now_ts = int(time.time())
            days_left = max(0, (expire_ts - now_ts) // 86400) if expire_ts else 0
            expire_date = time.strftime("%Y-%m-%d", time.localtime(expire_ts)) if expire_ts else "未知"
            return jsonify({
                "activated": True,
                "days_left": days_left,
                "expire_date": expire_date,
                "license_id": result.get("license_id", ""),
                "hw_id": lc.get_hardware_id(),
                "message": "激活成功",
            })
        else:
            return jsonify({
                "activated": False,
                "error_code": result.get("error_code", ""),
                "hw_id": lc.get_hardware_id(),
                "message": result.get("reason", "未激活"),
            })
    except Exception as e:
        return jsonify({"activated": False, "message": str(e)})


@app.route("/api/activate", methods=["POST"])
def api_activate():
    """使用激活码在线激活（可选，用于Web界面手动激活）"""
    data = request.json or {}
    code = data.get("code", "").strip().upper()
    if not code:
        return jsonify({"success": False, "message": "请输入激活码"})
    try:
        lc = _get_license_client()
        result = lc.activate(code)
        if result["ok"]:
            return jsonify({"success": True, "message": f"激活成功，到期时间: {result['expire_at']}"})
        else:
            return jsonify({"success": False, "message": result.get("error", "激活失败")})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# --- 运行 ---

@app.route("/api/run/manual", methods=["POST"])
def api_manual_run():
    global _manual_stop_event, _manual_running

    data = request.json or {}
    date_from = data.get("date_from", "")
    date_to = data.get("date_to", "")
    run_type = "手动运行"

    config = _load_config()

    # 验证微信昵称（必填）
    my_name = config.get("wechat", {}).get("my_name", "")
    if not my_name:
        return jsonify({"success": False, "message": "请先配置微信昵称"})

    # 仅在开启 AI 总结时要求 API Key（使用本地模型时无需 API Key）
    llm_enabled = config.get("llm_summary_enabled", True)
    use_local_llm = config.get("llm", {}).get("local_model", {}).get("enabled", False)
    if llm_enabled and not use_local_llm:
        api_key = config.get("llm", {}).get("api_key", "")
        if not api_key:
            return jsonify({"success": False, "message": "AI 总结已开启，请先配置 API Key（或开启本地模型 / 关闭 AI 总结后运行）"})

    now = datetime.now()
    if date_from:
        try:
            since_dt = datetime.strptime(date_from, "%Y-%m-%d %H:%M")
        except ValueError:
            return jsonify({"success": False, "message": "日期格式错误，示例: 2026-06-24 09:00"})
    else:
        since_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if date_to:
        try:
            to_dt = datetime.strptime(date_to, "%Y-%m-%d %H:%M")
        except ValueError:
            return jsonify({"success": False, "message": "结束日期格式错误，示例: 2026-06-24 18:00"})
        if to_dt > now:
            return jsonify({"success": False, "message": "结束时间不能是未来"})
        if to_dt < since_dt:
            return jsonify({"success": False, "message": "结束时间不能早于开始时间"})
    else:
        to_dt = now

    date_to_str = to_dt.strftime("%Y-%m-%d %H:%M")
    date_from_str = since_dt.strftime("%Y-%m-%d %H:%M")

    _progress_sink(f"手动运行: 从 {date_from_str} 到 {date_to_str}")

    # 创建停止事件
    _manual_stop_event = threading.Event()
    _manual_running = True
    stop_ev = _manual_stop_event  # 捕获引用

    def _run():
        global _last_run_time, _current_stats, _manual_running
        try:
            # 互斥锁：防止与自动运行同时执行
            if not _daily_run_lock.acquire(blocking=False):
                _progress_sink("⏭ 手动运行已跳过：自动运行任务正在执行，请稍后再试")
                return
            try:
                from app.engine import run_daily
                result = run_daily(
                    config=config,
                    date_from=date_from_str,
                    date_to=date_to_str,
                    run_type=run_type,
                    progress=_progress_sink,
                    stop_event=stop_ev,
                )
                if stop_ev.is_set():
                    _progress_sink("⏹ 手动运行已被用户停止")
                    return
                if result.get("success"):
                    _last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                    _current_stats = result.get("stats", {})
                    if result.get("filepath"):
                        _progress_sink("手动运行完成 ✅")
                    else:
                        _progress_sink(f"手动运行完成（未生成文件: {result.get('message', '未知')}）")
                else:
                    _progress_sink(f"手动运行失败: {result.get('message', '未知错误')}")
            finally:
                _daily_run_lock.release()
        except Exception as e:
            logger.exception("手动运行线程异常")
            _progress_sink(f"手动运行异常: {e}")
            _progress_sink("请检查 logs/ 目录下的日志文件获取详细错误信息")
        finally:
            _manual_running = False

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({"success": True, "message": "已开始运行"})


@app.route("/api/run/manual/stop", methods=["POST"])
def api_manual_stop():
    global _manual_stop_event, _manual_running
    if _manual_stop_event and _manual_running:
        _manual_stop_event.set()
        _progress_sink("⏹ 正在停止手动运行...")
        return jsonify({"success": True, "message": "正在停止"})
    return jsonify({"success": False, "message": "没有正在运行的手动任务"})


@app.route("/api/run/auto/start", methods=["POST"])
def api_auto_start():
    global _scheduler

    with _scheduler_lock:
        if _scheduler and _scheduler.is_running:
            return jsonify({"success": True, "message": "调度器已在运行"})

        def on_schedule():
            _run_daily_task("自动运行")

        # 使用 config_provider 动态获取配置，而非传入静态 dict
        _scheduler = DailyScheduler(_load_config, on_schedule)
        _scheduler.start()

    return jsonify({"success": True, "message": "调度器已启动"})


@app.route("/api/run/auto/stop", methods=["POST"])
def api_auto_stop():
    global _scheduler
    with _scheduler_lock:
        if _scheduler:
            _scheduler.stop()
            _scheduler = None
    return jsonify({"success": True, "message": "调度器已停止"})


@app.route("/api/run/status", methods=["GET"])
def api_run_status():
    global _scheduler, _last_run_time, _manual_running, _analyze_running
    with _scheduler_lock:
        is_running = _scheduler is not None and _scheduler.is_running
        next_run = _scheduler.next_run if is_running else None
    return jsonify({
        "auto_running": is_running,
        "next_run": next_run,
        "last_run": _last_run_time,
        "stats": _current_stats,
        "manual_running": _manual_running,
        "analyze_running": _analyze_running,
    })


# --- SSE 实时日志 ---

@app.route("/api/log/stream")
def api_log_stream():
    def generate():
        # 先发送连接建立消息
        yield f"data: {json.dumps({'time': datetime.now().strftime('%H:%M:%S'), 'text': '系统已就绪，请确保微信正在运行，然后配置昵称和 API Key 后点击「手动运行」'}, ensure_ascii=False)}\n\n"

        # 再发送历史日志
        history = _log_queue.read_all()
        for item in history:
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

        # 持续监听新日志（阻塞读，无日志时不占用 CPU）
        while True:
            try:
                item = _log_queue.queue.get(timeout=2)  # 阻塞最多 2 秒
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                # 批量取出队列中剩余的（非阻塞）
                for extra in _log_queue.read_all():
                    yield f"data: {json.dumps(extra, ensure_ascii=False)}\n\n"
            except Exception:
                # queue.Empty 超时，继续等待
                pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "Connection": "keep-alive"})


# --- 周报 ---

@app.route("/api/weekly", methods=["POST"])
def api_weekly():
    data = request.json or {}
    week_ref = data.get("week", "")  # 可指定日期
    config = _load_config()

    _progress_sink("正在生成周报...")

    def _run():
        from app.engine import run_weekly
        result = run_weekly(config, week_ref or None, progress=_progress_sink)
        if result.get("success"):
            _progress_sink("周报生成完成 ✅")
        else:
            _progress_sink(f"周报生成失败: {result.get('message', '')}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({"success": True, "message": "周报生成已开始"})


def _auto_detect_wxid_and_db() -> tuple:
    """自动检测 wxid 和数据库目录，返回 (wxid, db_dir)

    扫描常见微信数据根目录，识别微信账号目录（含 db_storage/Msg/msg 子目录）。
    兼容微信 3.x (wxid_xxx) 与 4.x (自定义 wxid，如 xxx_yyy) 的目录命名。
    多个候选时优先返回最近修改的账号目录。
    """
    userprofile = os.environ.get("USERPROFILE", "")

    # 候选根目录：Documents 标准位置 + 各盘符根目录
    base_dirs = []
    if userprofile:
        base_dirs += [
            os.path.join(userprofile, "Documents", "WeChat Files"),
            os.path.join(userprofile, "Documents", "xwechat_files"),
        ]
    for drive in ("C:", "D:", "E:", "F:"):
        for sub in ("WeChat Files", "xwechat_files"):
            base_dirs.append(os.path.join(drive, sub))

    candidates = []  # [(mtime, wxid, db_dir), ...]
    seen_paths = set()

    for base in base_dirs:
        if not os.path.isdir(base):
            continue
        try:
            for entry in os.listdir(base):
                if entry.lower() in _WX_SYSTEM_DIRS:
                    continue
                full = os.path.join(base, entry)
                if not os.path.isdir(full):
                    continue
                # 含数据库子目录即认定为账号目录，entry 即为 wxid
                for sub in ("db_storage", "Msg", "msg"):
                    p = os.path.join(full, sub)
                    if os.path.isdir(p):
                        norm = os.path.normpath(p).lower()
                        if norm in seen_paths:
                            break
                        seen_paths.add(norm)
                        try:
                            mtime = os.path.getmtime(p)
                        except OSError:
                            mtime = 0
                        candidates.append((mtime, entry, p))
                        break
        except (PermissionError, OSError):
            continue

    if not candidates:
        return "", ""

    # 按修改时间降序，优先返回最近活跃的账号
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1], candidates[0][2]


# 微信数据库子目录名（小写匹配，兼容 Msg/msg/db_storage）
_DB_SUBDIRS = {"db_storage", "msg"}

# 微信系统目录（非账号目录），排除
_WX_SYSTEM_DIRS = {
    "all users", "applet", "wmpf", "wechatappex", "wechat files",
    "xwechat_files", "multisearch", "drivers", "download",
    "globalimage", "help", "icon", "config", "logs", "backupfiles",
}


def _detect_wxid_from_dbdir(db_dir: str) -> str:
    """从数据库目录路径推断 wxid

    微信数据库目录标准结构：
    - 4.x: .../xwechat_files/{wxid}/db_storage
    - 3.x: .../WeChat Files/{wxid}/Msg

    推断规则（按优先级）：
    1. db_dir 末尾是数据库子目录(db_storage/Msg/msg) → wxid = 上一级目录名
    2. db_dir 本身含数据库子目录（即 db_dir 是账号目录）→ wxid = db_dir 最后一级
    3. 向上最多回溯 2 级查找含数据库子目录的账号目录

    所有情况均排除系统目录名，确保不误识别。
    目录不存在或无权限时返回空字符串。
    """
    if not db_dir:
        return ""

    db_dir = os.path.normpath(db_dir)
    if not os.path.isdir(db_dir):
        return ""

    parts = db_dir.split(os.sep)
    # 过滤掉空元素（如开头的盘符 C:\ split 后会有空串）
    parts = [p for p in parts if p]
    if not parts:
        return ""

    basename = parts[-1]

    # 规则1: 末尾是数据库子目录，wxid = 上一级
    if basename.lower() in _DB_SUBDIRS and len(parts) >= 2:
        candidate = parts[-2]
        if candidate and candidate.lower() not in _WX_SYSTEM_DIRS:
            return candidate

    # 规则2: db_dir 本身是账号目录（含数据库子目录）
    for sub in ("db_storage", "Msg", "msg"):
        if os.path.isdir(os.path.join(db_dir, sub)):
            if basename and basename.lower() not in _WX_SYSTEM_DIRS:
                return basename
            break

    # 规则3: 向上回溯最多 2 级，查找含数据库子目录的账号目录
    # 适用：db_dir 指向了更深层的子目录（如 db_storage/message）
    current = db_dir
    for _ in range(2):
        parent = os.path.dirname(current)
        if parent == current:
            break  # 到达根目录
        parent_name = os.path.basename(parent)
        if parent_name and parent_name.lower() not in _WX_SYSTEM_DIRS:
            for sub in ("db_storage", "Msg", "msg"):
                if os.path.isdir(os.path.join(parent, sub)):
                    return parent_name
        current = parent

    return ""


@app.route("/api/debug/db-dir", methods=["GET"])
def api_debug_db_dir():
    """诊断数据库目录内容"""
    db_dir = request.args.get("dir", "")
    if not db_dir:
        db_dir = os.path.expanduser("~/.wechat-cli")
        try:
            with open(os.path.join(db_dir, "config.json")) as f:
                db_dir = json.load(f).get("db_dir", "")
        except Exception:
            pass
    info = {"db_dir": db_dir, "exists": os.path.isdir(db_dir), "files": []}
    if os.path.isdir(db_dir):
        for root, _dirs, _files in os.walk(db_dir):
            for f in _files:
                p = os.path.join(root, f)
                try:
                    info["files"].append({"name": f, "size_kb": os.path.getsize(p) // 1024, "rel": os.path.relpath(p, db_dir)})
                except OSError:
                    info["files"].append({"name": f, "size_kb": -1, "rel": "?"})
    return jsonify(info)


@app.route("/api/init/detect-wxid", methods=["POST"])
def api_detect_wxid():
    """根据数据库目录路径自动识别 wxid

    供前端基本设置页使用：用户填了数据库目录后，点击"识别ID"按钮
    自动从路径结构中提取 wxid 并回填到微信ID输入框。
    """
    data = request.json or {}
    db_dir = (data.get("db_dir") or "").strip()
    if not db_dir:
        return jsonify({"success": False, "wxid": "", "message": "请先填写数据库目录"})
    if not os.path.isdir(db_dir):
        return jsonify({"success": False, "wxid": "", "message": f"目录不存在: {db_dir}"})

    wxid = _detect_wxid_from_dbdir(db_dir)
    if wxid:
        # 回写到 config.yaml（仅当 wxid 为空时填充，不覆盖用户已填值）
        try:
            _cfg = _load_config()
            _cfg.setdefault("wechat", {})
            if not _cfg["wechat"].get("wxid"):
                _cfg["wechat"]["wxid"] = wxid
                _save_config(_cfg)
        except Exception as _e:
            logger.warning(f"回写 wxid 到配置失败: {_e}")
        return jsonify({"success": True, "wxid": wxid, "message": f"已识别微信ID: {wxid}"})
    else:
        return jsonify({"success": False, "wxid": "", "message": "无法从该目录路径识别微信ID，请手动填写"})


@app.route("/api/init/manual-key", methods=["POST"])
def api_init_manual_key():
    """手动密钥配置：只需 passphrase，自动检测 wxid 和数据库目录"""
    data = request.json or {}
    passphrase = (data.get("passphrase") or "").strip()
    db_dir = (data.get("db_dir") or "").strip()
    wxid = (data.get("wxid") or "").strip()

    if not passphrase:
        return jsonify({"success": False, "message": "请填写 passphrase（wx_key 提取的十六进制密钥）"})

    # 优先：从已填的 db_dir 路径推断 wxid（比全盘扫描更精准）
    if db_dir and not wxid:
        detected = _detect_wxid_from_dbdir(db_dir)
        if detected:
            wxid = detected
            _progress_sink(f"从数据库目录识别 wxid: {wxid}")

    # 兜底：wxid 或 db_dir 仍为空，全盘扫描
    if not wxid or not db_dir:
        auto_wxid, auto_db = _auto_detect_wxid_and_db()
        wxid = wxid or auto_wxid
        db_dir = db_dir or auto_db
        if wxid:
            _progress_sink(f"自动检测: wxid={wxid}")
        if db_dir:
            _progress_sink(f"自动检测: db_dir={db_dir}")

    # 回写到 config.yaml，避免基本设置仍为空（仅填充空字段，不覆盖用户已填值）
    try:
        _cfg = _load_config()
        _cfg.setdefault("wechat", {})
        changed = False
        if wxid and not _cfg["wechat"].get("wxid"):
            _cfg["wechat"]["wxid"] = wxid
            changed = True
        if db_dir and not _cfg["wechat"].get("db_dir"):
            _cfg["wechat"]["db_dir"] = db_dir
            changed = True
        if changed:
            _save_config(_cfg)
            _progress_sink("已将检测到的 wxid 和数据库目录写入基本设置")
    except Exception as _e:
        logger.warning(f"回写 wxid/db_dir 到配置失败: {_e}")

    if not wxid:
        return jsonify({"success": False, "message": "未检测到 wxid，请确认微信数据目录存在"})
    if not db_dir or not os.path.isdir(db_dir):
        return jsonify({"success": False, "message": f"数据库目录不存在，请手动填写"})

    try:
        bytes.fromhex(passphrase)
    except ValueError:
        return jsonify({"success": False, "message": "passphrase 格式错误，应为十六进制字符串"})

    # 密钥派生：wx_key 提取的是 passphrase（不是最终加密密钥）
    # WeChat 4.1+ 数据库加密流程: enc_key = PBKDF2-HMAC-SHA512(passphrase, salt, 256000, 32)
    # 然后验证 enc_key 能否正确解密 page 1 的 HMAC
    import hashlib
    import hmac as hmac_mod
    import struct
    wechat_cli_dir = os.path.expanduser("~/.wechat-cli")
    os.makedirs(wechat_cli_dir, exist_ok=True)

    PAGE_SZ = 4096
    SALT_SZ = 16
    KEY_SZ = 32
    RESERVE_SZ = 80  # IV(16) + HMAC(64)
    PBKDF2_ITER = 256000

    passphrase_bytes = bytes.fromhex(passphrase)
    db_files: dict[str, dict] = {}
    db_lock = threading.Lock()
    found_all: list[str] = []
    verified, failed, skipped = 0, 0, 0

    # 收集所有待处理的 .db 文件
    db_tasks: list[tuple[str, str, int]] = []  # (path, rel, size)
    for root, _dirs, files in os.walk(db_dir):
        for name in files:
            if name.endswith(".db") and not name.endswith("-wal") and not name.endswith("-shm"):
                path = os.path.join(root, name)
                try:
                    sz = os.path.getsize(path)
                    found_all.append(f"{name}({sz//1024}KB)")
                    if sz >= PAGE_SZ:
                        rel = os.path.relpath(path, db_dir)
                        db_tasks.append((path, rel, sz))
                except OSError:
                    found_all.append(f"{name}(权限拒绝)")
                    continue

    def _derive_one(path: str, rel: str, sz: int):
        """单个数据库的 PBKDF2 派生（在线程池中并行执行）"""
        try:
            with open(path, "rb") as dbf:
                page1 = dbf.read(PAGE_SZ)
        except (OSError, PermissionError):
            return ("skip", rel, {"enc_key": passphrase, "salt": "", "size_mb": round(sz / 1024 / 1024, 1)})

        if len(page1) < SALT_SZ:
            return ("none", rel, None)

        salt = page1[:SALT_SZ]
        salt_hex = salt.hex()

        # 派生加密密钥（256000 次迭代，CPU 密集型）
        enc_key = hashlib.pbkdf2_hmac("sha512", passphrase_bytes, salt, PBKDF2_ITER, dklen=KEY_SZ)

        # 验证：page 1 HMAC 校验
        mac_salt = bytes(b ^ 0x3A for b in salt)
        mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
        hmac_data = page1[SALT_SZ: PAGE_SZ - RESERVE_SZ + SALT_SZ]
        stored_hmac = page1[PAGE_SZ - 64: PAGE_SZ]
        hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
        hm.update(struct.pack("<I", 1))

        if hm.digest() == stored_hmac:
            return ("ok", rel, {"enc_key": enc_key.hex(), "salt": salt_hex, "size_mb": round(sz / 1024 / 1024, 1)})
        else:
            return ("fail", rel, {"enc_key": enc_key.hex(), "salt": salt_hex, "size_mb": round(sz / 1024 / 1024, 1)})

    # 并行派生（利用多核加速）
    max_workers = min(os.cpu_count() or 4, max(1, len(db_tasks)))
    _progress_sink(f"并行派生 {len(db_tasks)} 个数据库密钥（{max_workers} 线程）...")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_derive_one, path, rel, sz): rel for path, rel, sz in db_tasks}
        for future in as_completed(futures):
            status, rel, result = future.result()
            with db_lock:
                if status == "ok":
                    verified += 1
                    db_files[rel] = result
                elif status == "fail":
                    failed += 1
                    db_files[rel] = result
                elif status == "skip":
                    skipped += 1
                    db_files[rel] = result

    if skipped:
        _progress_sink(f"警告: {skipped} 个数据库因微信锁定无法读取，请关闭微信后重新配置")

    _progress_sink(f"密钥派生: {verified} 个验证通过" + (f", {failed} 个失败" if failed else ""))

    if not db_files:
        detail = f"目录 {db_dir} 下扫描到的 .db 文件: " + (", ".join(found_all) if found_all else "无")
        logger.warning(detail)
        return jsonify({"success": False, "message": f"数据库目录中未找到可用的 .db 文件\n{detail}"})

    # 写 all_keys.json（wechat-cli 兼容格式: {相对路径: {"enc_key": "...", "salt": "...", "size_mb": N}}）
    keys_path = os.path.join(wechat_cli_dir, "all_keys.json")
    with open(keys_path, "w", encoding="utf-8") as f:
        json.dump(db_files, f, indent=2, ensure_ascii=False)

    # 兼容 account 子目录（wechat-cli 多账号模式使用 keys.json + config.json）
    acc_dir = os.path.join(wechat_cli_dir, "accounts", wxid)
    os.makedirs(acc_dir, exist_ok=True)
    with open(os.path.join(acc_dir, "keys.json"), "w", encoding="utf-8") as f:
        json.dump(db_files, f, indent=2, ensure_ascii=False)
    with open(os.path.join(acc_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"db_dir": db_dir}, f, indent=2)

    # config.json
    with open(os.path.join(wechat_cli_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"db_dir": db_dir}, f, indent=2)

    _progress_sink(f"密钥配置完成，{len(db_files)} 个数据库已关联，保存至: {keys_path}")
    return jsonify({"success": True, "message": f"密钥配置成功！{len(db_files)} 个数据库已关联"})


@app.route("/api/init/test-keys", methods=["POST"])
def api_test_keys():
    """测试当前密钥是否有效（快速 smoke test，不读取大量数据）"""
    try:
        from wechat_reader import WeChatReader
        config = _load_config()
        reader = WeChatReader(config)
        result = reader.test_keys()
        if result.get("ok"):
            _progress_sink(f"[密钥测试] {result.get('message', '')}")
        else:
            _progress_sink(f"[密钥测试] ❌ {result.get('message', '')}")
        return jsonify(result)
    except Exception as e:
        logger.exception("密钥测试异常")
        return jsonify({"ok": False, "message": f"测试异常: {e}", "session_count": 0, "error_detail": str(e)})


@app.route("/api/sessions/list", methods=["GET"])
def api_sessions_list():
    """获取所有有效会话列表（供「会话分析」选择器使用）"""
    try:
        from chat_analyzer import ChatAnalyzer
        config = _load_config()
        analyzer = ChatAnalyzer(config)
        sessions = analyzer._get_sessions()
        return jsonify({"success": True, "sessions": sessions})
    except Exception as e:
        logger.exception("获取会话列表失败")
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/analyze/chat", methods=["POST"])
def api_analyze_chat():
    """执行重点会话分析"""
    global _analyze_stop_event, _analyze_running

    data = request.json or {}
    chat_names = data.get("chats", [])
    date_from = data.get("date_from", "")
    date_to = data.get("date_to", "")
    requirement = (data.get("requirement") or "").strip()
    if not chat_names:
        return jsonify({"success": False, "message": "请选择至少一个会话"})

    config = _load_config()
    now = datetime.now()
    if date_from:
        try:
            since_dt = datetime.strptime(date_from, "%Y-%m-%d %H:%M")
        except ValueError:
            return jsonify({"success": False, "message": "日期格式错误"})
    else:
        since_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if date_to:
        try:
            to_dt = datetime.strptime(date_to, "%Y-%m-%d %H:%M")
        except ValueError:
            return jsonify({"success": False, "message": "结束日期格式错误"})
        if to_dt > now:
            return jsonify({"success": False, "message": "结束时间不能是未来"})
        if to_dt < since_dt:
            return jsonify({"success": False, "message": "结束时间不能早于开始时间"})
    else:
        to_dt = None

    _progress_sink(f"开始分析 {len(chat_names)} 个会话: {', '.join(chat_names[:5])}..."
                   + (f"\n  分析要求: {requirement[:80]}" if requirement else ""))

    _analyze_stop_event = threading.Event()
    _analyze_running = True
    stop_ev = _analyze_stop_event

    def _run():
        global _analyze_running, _last_analyze_report
        try:
            from chat_analyzer import ChatAnalyzer
            analyzer = ChatAnalyzer(config)
            result = analyzer.analyze(
                chat_names, since_dt, to_dt,
                requirement=requirement or None,
                progress=_progress_sink,
                stop_event=stop_ev,
            )
            if stop_ev.is_set():
                _progress_sink("⏹ 会话分析已被用户停止")
                return
            if result.get("success"):
                fp = result.get("filepath", "")
                if fp:
                    _last_analyze_report = fp
                _progress_sink(f"✅ 分析完成！→ {fp}")
            else:
                _progress_sink(f"❌ 分析失败: {result.get('message', '')}")
        except Exception as e:
            logger.exception("会话分析异常")
            _progress_sink(f"分析异常: {e}")
        finally:
            _analyze_running = False

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({"success": True, "message": "已开始分析"})


@app.route("/api/analyze/chat/stop", methods=["POST"])
def api_analyze_chat_stop():
    global _analyze_stop_event, _analyze_running
    if _analyze_stop_event and _analyze_running:
        _analyze_stop_event.set()
        _progress_sink("⏹ 正在停止会话分析...")
        return jsonify({"success": True, "message": "正在停止"})
    return jsonify({"success": False, "message": "没有正在运行的会话分析"})


@app.route("/api/analyze/chat/latest", methods=["GET"])
def api_analyze_chat_latest():
    """获取最新会话分析报告路径"""
    return jsonify({"path": _last_analyze_report})


# ========== 会话监控 ==========

# 全局调度器状态（延迟导入避免循环依赖，类型标注用字符串）
_monitor_scheduler = None  # 类型: ChatMonitorScheduler
_monitor_scheduler_lock = threading.Lock()
_monitor_running = False
_monitor_stop_event: Optional[threading.Event] = None


def _run_monitor_task():
    """执行一次会话监控运行"""
    global _monitor_running, _monitor_stop_event

    if not _daily_run_lock.acquire(blocking=False):
        _progress_sink("⏭ 会话监控已跳过：另一个任务正在运行")
        return

    _monitor_running = True
    _monitor_stop_event = threading.Event()
    stop_ev = _monitor_stop_event

    try:
        config = _load_config()
        from chat_monitor import ChatMonitor
        monitor = ChatMonitor(config)
        monitor.run(progress=_progress_sink, stop_event=stop_ev)
    except Exception as e:
        logger.exception("会话监控异常")
        _progress_sink(f"❌ 会话监控异常: {e}")
    finally:
        _monitor_running = False
        _daily_run_lock.release()


@app.route("/api/chat-monitor/config", methods=["GET"])
def api_chat_monitor_config():
    config = _load_config()
    mon = config.get("chat_monitor", {})
    return jsonify({
        "enabled": mon.get("enabled", False),
        "chats": mon.get("chats", []),
        "schedule_mode": mon.get("schedule", {}).get("mode", "interval"),
        "interval_seconds": mon.get("schedule", {}).get("interval_seconds", 3600),
        "scheduled_time": mon.get("schedule", {}).get("scheduled_time", "17:30"),
        "first_run_hours": mon.get("fetch", {}).get("first_run_hours", 24),
    })


@app.route("/api/chat-monitor/config", methods=["POST"])
def api_chat_monitor_save():
    data = request.json or {}
    config = _load_config()
    config["chat_monitor"] = {
        "enabled": data.get("enabled", False),
        "chats": data.get("chats", []),
        "schedule": {
            "mode": data.get("schedule_mode", "interval"),
            "interval_seconds": data.get("interval_seconds", 3600),
            "scheduled_time": data.get("scheduled_time", "17:30"),
        },
        "fetch": {
            "first_run_hours": data.get("first_run_hours", 24),
        },
    }
    _save_config(config)
    return jsonify({"success": True, "message": "配置已保存"})


@app.route("/api/chat-monitor/start", methods=["POST"])
def api_chat_monitor_start():
    global _monitor_scheduler
    with _monitor_scheduler_lock:
        if _monitor_scheduler and _monitor_scheduler.is_running:
            return jsonify({"success": True, "message": "监控已在运行"})
        from chat_monitor import ChatMonitorScheduler
        _monitor_scheduler = ChatMonitorScheduler(_load_config, _run_monitor_task)
        _monitor_scheduler.start()
    return jsonify({"success": True, "message": "会话监控已启动"})


@app.route("/api/chat-monitor/stop", methods=["POST"])
def api_chat_monitor_stop():
    global _monitor_scheduler
    with _monitor_scheduler_lock:
        if _monitor_scheduler:
            _monitor_scheduler.stop()
            _monitor_scheduler = None
    return jsonify({"success": True, "message": "会话监控已停止"})


@app.route("/api/chat-monitor/status", methods=["GET"])
def api_chat_monitor_status():
    global _monitor_scheduler, _monitor_running
    is_running = _monitor_scheduler is not None and _monitor_scheduler.is_running
    next_run = _monitor_scheduler.next_run if is_running else None
    last_run = _monitor_scheduler.last_run if _monitor_scheduler else None
    return jsonify({
        "scheduler_running": is_running,
        "task_running": _monitor_running,
        "next_run": next_run,
        "last_run": last_run,
    })


@app.route("/api/chat-monitor/run-now", methods=["POST"])
def api_chat_monitor_run_now():
    """立即执行一次监控"""
    global _monitor_running
    if _monitor_running:
        return jsonify({"success": False, "message": "监控正在运行中"})
    thread = threading.Thread(target=_run_monitor_task, daemon=True)
    thread.start()
    return jsonify({"success": True, "message": "已开始运行"})


@app.route("/api/chat-monitor/stop-task", methods=["POST"])
def api_chat_monitor_stop_task():
    global _monitor_stop_event, _monitor_running
    if _monitor_stop_event and _monitor_running:
        _monitor_stop_event.set()
        _progress_sink("⏹ 正在停止会话监控...")
        return jsonify({"success": True, "message": "正在停止"})
    return jsonify({"success": False, "message": "没有正在运行的监控任务"})


@app.route("/api/chat-monitor/files", methods=["GET"])
def api_chat_monitor_files():
    """列出所有监控生成的 .md 文件"""
    from chat_monitor import ChatMonitor
    output_dir = _get_output_dir()
    files = ChatMonitor.list_monitor_files(output_dir)
    return jsonify({"success": True, "files": files})


@app.route("/api/chat-monitor/analyze", methods=["POST"])
def api_chat_monitor_analyze():
    """手动选文件发送给 LLM 分析"""
    data = request.json or {}
    filepaths = data.get("files", [])
    requirement = (data.get("requirement") or "").strip()

    if not filepaths:
        return jsonify({"success": False, "message": "请选择至少一个文件"})

    def _run():
        try:
            config = _load_config()
            from chat_monitor import ChatMonitor
            monitor = ChatMonitor(config)
            result = monitor.analyze_files(filepaths, requirement, progress=_progress_sink)
            if result.get("success"):
                _progress_sink("✅ 分析完成")
            else:
                _progress_sink(f"❌ 分析失败: {result.get('message', '')}")
        except Exception as e:
            logger.exception("会话分析异常")
            _progress_sink(f"❌ 分析异常: {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({"success": True, "message": "分析已开始"})


@app.route("/api/quit", methods=["POST"])
def api_quit():
    """安全退出程序"""
    import threading

    def _shutdown():
        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({"success": True, "message": "正在退出..."})


@app.route("/api/update/check", methods=["GET"])
def api_update_check():
    """手动检查更新，返回版本信息和是否有更新"""
    import json as _json
    try:
        req = urllib.request.Request(
            "https://raw.githubusercontent.com/woshinengdadie/weixinribao/main/updates.json"
        )
        req.add_header("User-Agent", "WeChatWorkAgent/check")
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return jsonify({"success": False, "message": f"检查更新失败: {str(e)[:200]}"})

    remote_ver = data.get("version", "0")
    def _parse(v):
        try: return tuple(int(x) for x in v.strip().split("."))
        except: return (0,)

    local_ver_str = _read_version()

    has_update = _parse(remote_ver) > _parse(local_ver_str)
    return jsonify({
        "success": True,
        "has_update": has_update,
        "current_version": local_ver_str,
        "latest_version": remote_ver,
        "notes": data.get("notes", ""),
        "url": data.get("url", ""),
        "date": data.get("date", ""),
    })


def _read_version() -> str:
    """从多位置查找 VERSION 文件，兼容 dev / PyInstaller 打包环境"""
    candidates = [
        # 1) PyInstaller 打包：_MEIPASS 指向 _internal/ 目录
        os.path.join(getattr(sys, "_MEIPASS", ""), "VERSION"),
        # 2) 打包后同 exe 目录（COLLECT 模式可能在此）
        os.path.join(os.path.dirname(sys.executable), "VERSION") if getattr(sys, "frozen", False) else "",
        # 3) 源码模式：项目根
        os.path.join(PROJECT_ROOT, "VERSION"),
        # 4) 兜底：从本文件向上 1 级
        os.path.join(os.path.dirname(__file__), "..", "VERSION"),
    ]
    for vfile in candidates:
        if vfile and os.path.exists(vfile):
            try:
                return open(vfile, encoding="utf-8").read().strip()
            except Exception:
                continue
    return "unknown"


@app.route("/api/version", methods=["GET"])
def api_version():
    """返回当前程序版本号（用于顶栏显示）"""
    return jsonify({"success": True, "version": _read_version()})


@app.route("/api/weekly/list", methods=["GET"])
def api_weekly_list():
    reports = _get_weekly_reports()
    folders = _get_output_folders()
    return jsonify({
        "weekly_reports": [_["name"] for _ in reports],
        "output_folders": [_["name"] for _ in folders],
    })


# --- 清理待办旧数据 ---

@app.route("/api/todos/clear", methods=["POST"])
def api_todos_clear():
    """清除 all_todos.json 和 待办事项.html"""
    output_dir = _get_output_dir()
    todos_json = os.path.join(output_dir, "all_todos.json")
    todos_html = os.path.join(output_dir, "待办事项.html")

    deleted = []
    for f in [todos_json, todos_html]:
        try:
            if os.path.exists(f):
                os.remove(f)
                deleted.append(os.path.basename(f))
        except Exception as e:
            logger.warning(f"删除 {f} 失败: {e}")

    if deleted:
        return jsonify({"success": True, "message": f"已清理: {', '.join(deleted)}"})
    else:
        return jsonify({"success": True, "message": "没有需要清理的数据"})


# --- 打开文件/文件夹 ---

@app.route("/api/file/open", methods=["POST"])
def api_file_open():
    data = request.json or {}
    path = data.get("path", "")

    if not path:
        # 默认打开 output 目录
        path = _get_output_dir()

    # URL：用浏览器打开（用于打开下载页、文档链接等）
    if path.startswith(("http://", "https://")):
        try:
            import webbrowser
            webbrowser.open(path)
            return jsonify({"success": True, "type": "url"})
        except Exception as e:
            return jsonify({"success": False, "message": f"打开URL失败: {e}"})

    # 本地路径：限制仅允许打开 output 目录下文件
    if not os.path.isabs(path):
        path = os.path.join(PROJECT_ROOT, path)
    abs_path = os.path.abspath(path)
    abs_output = os.path.abspath(_get_output_dir())
    if not abs_path.startswith(abs_output + os.sep) and abs_path != abs_output:
        return jsonify({"success": False, "message": "路径不在允许范围内"})

    try:
        if os.path.exists(path):
            os.startfile(path)
            return jsonify({"success": True, "type": "local"})
        return jsonify({"success": False, "message": f"路径不存在: {path}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/file/pick-folder", methods=["POST"])
def api_file_pick_folder():
    """打开系统文件夹选择对话框，返回用户选择的路径"""
    import tkinter as tk
    from tkinter import filedialog

    # 在后台线程中创建 tkinter 窗口（避免 Flask 主线程阻塞）
    # tkinter 需要在主线程运行，用 root.withdraw() 隐藏主窗口
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)

        # 初始目录：当前配置的输出目录
        initial_dir = _get_output_dir()
        if not os.path.isdir(initial_dir):
            initial_dir = PROJECT_ROOT

        selected = filedialog.askdirectory(
            title="选择文档输出目录",
            initialdir=initial_dir,
            parent=root
        )
        root.destroy()

        if selected:
            return jsonify({"success": True, "path": selected})
        else:
            return jsonify({"success": False, "path": ""})
    except Exception as e:
        logger.error(f"文件夹选择对话框失败: {e}")
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/file/pick-files", methods=["POST"])
def api_file_pick_files():
    """打开系统多选文件对话框，返回用户选择的 .md 文件路径列表"""
    import tkinter as tk
    from tkinter import filedialog

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)

        # 初始目录：监控输出目录 或 用户配置的输出目录
        output_dir = _get_output_dir()
        monitor_dir = os.path.join(output_dir, "会话监控")
        initial_dir = monitor_dir if os.path.isdir(monitor_dir) else output_dir
        if not os.path.isdir(initial_dir):
            initial_dir = PROJECT_ROOT

        selected = filedialog.askopenfilenames(
            title="选择消息文件（.md）",
            initialdir=initial_dir,
            parent=root,
            filetypes=[("Markdown 文件", "*.md"), ("所有文件", "*.*")],
        )
        root.destroy()

        # askopenfilenames 返回元组，转为绝对路径列表
        files = [os.path.abspath(f) for f in selected]
        return jsonify({"success": True, "files": files, "count": len(files)})
    except Exception as e:
        logger.error(f"文件选择对话框失败: {e}")
        return jsonify({"success": False, "message": str(e)})


# --- 获取最新文件路径 ---

@app.route("/api/file/latest", methods=["GET"])
def api_file_latest():
    """获取最新生成的文件路径"""
    file_type = request.args.get("type", "summary")
    output_dir = _get_output_dir()
    default_output_dir = os.path.join(PROJECT_ROOT, "output")

    # 待办事项：优先查用户配置的输出目录
    if file_type == "todos":
        for d in [output_dir, default_output_dir]:
            path = os.path.join(d, "待办事项.html")
            if os.path.exists(path):
                return jsonify({"path": path})
        return jsonify({"path": ""})

    # 周报
    if file_type == "weekly":
        reports = _get_weekly_reports()
        if reports:
            return jsonify({"path": reports[-1]["path"]})
        return jsonify({"path": ""})

    # 文件夹/摘要/详情：用最新运行目录
    folders = _get_output_folders()
    if not folders:
        return jsonify({"path": ""})

    latest = folders[-1]
    folder_path = latest["path"]  # 使用 _get_output_folders 返回的完整路径

    if file_type == "folder":
        return jsonify({"path": folder_path})

    # summary 或 detail
    files = os.listdir(folder_path) if os.path.exists(folder_path) else []
    for f in files:
        if file_type == "summary" and f.startswith("工作总结_"):
            return jsonify({"path": os.path.join(folder_path, f)})
        if file_type == "detail" and f.startswith("对话详情_"):
            return jsonify({"path": os.path.join(folder_path, f)})

    return jsonify({"path": ""})


def create_app():
    """创建并返回 Flask app 实例"""
    return app


if __name__ == "__main__":
    # 直接调试模式启动 Flask（不依赖 PyWebView）
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    port = int(os.environ.get("PORT", 5566))
    logger.info(f"Flask 调试服务启动于 http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
