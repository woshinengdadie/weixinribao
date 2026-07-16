# pyright: reportExplicitAny=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAny=false
"""微信工作日报助手 - 主入口

用法：
    python main.py                  # 读取今日消息并生成日报
    python main.py --date 2026-06-17  # 指定日期
    python main.py --config config/my_config.yaml  # 指定配置文件
    python main.py --init           # 初始化 wechat-cli
    python main.py --watch          # 持续监听（每隔N分钟检查一次）
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime

# 延迟导入，避免循环依赖
def _import():
    global WeChatReader, MessageFilter, Summarizer, ReportGenerator
    from src.wechat_reader import WeChatReader
    from src.message_filter import MessageFilter
    from src.summarizer import Summarizer
    from src.report_generator import ReportGenerator
    return WeChatReader, MessageFilter, Summarizer, ReportGenerator

def _import_weekly():
    """延迟导入周报模块"""
    global WeeklyReportGenerator
    from src.weekly_report_generator import WeeklyReportGenerator
    return WeeklyReportGenerator


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """加载配置文件，支持环境变量覆盖敏感字段"""
    import shutil as _shutil
    import yaml

    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "config", "config.yaml")

    if not os.path.exists(config_path):
        example_path = os.path.join(os.path.dirname(config_path), "config.yaml.example")
        if os.path.exists(example_path):
            _ = _shutil.copy(example_path, config_path)
            print(f"\u2139\ufe0f 已从 config.yaml.example 创建 config.yaml，请修改配置后使用")
        else:
            print(f"\u274c 配置文件不存在: {config_path}")
            sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config: dict[str, Any] = cast(dict[str, Any], yaml.safe_load(f))

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


def validate_config(config: dict[str, Any]) -> None:
    """启动时验证关键配置字段，提前给出清晰错误提示"""
    errors: list[str] = []

    # 微信配置检查
    wechat: dict[str, Any] = config.get("wechat", {})
    db_dir: str = str(wechat.get("db_dir", ""))
    if not db_dir:
        errors.append("缺少 wechat.db_dir 配置，请指定微信数据库路径")
    elif not os.path.exists(db_dir):
        errors.append(f"微信数据库路径不存在: {db_dir}")

    my_name: str = str(wechat.get("my_name", ""))
    if not my_name:
        errors.append("缺少 wechat.my_name 配置，请设置你的微信昵称")

    # LLM 配置检查
    llm_summary: bool = bool(config.get("report", {}).get("llm_summary_enabled", False))
    local_llm_enabled: bool = bool(config.get("local_llm", {}).get("enabled", False))
    if llm_summary and not local_llm_enabled:
        llm_config: dict[str, Any] = config.get("llm", {})
        api_key: str = str(llm_config.get("api_key", ""))
        if not api_key:
            errors.append("LLM 摘要已启用但未配置 llm.api_key，请设置 API 密钥")

    if errors:
        for err in errors:
            logger.error(f"配置验证失败: {err}")
            print(f"[配置错误] {err}")
        raise SystemExit(1)


def setup_logging(config: dict[str, Any]) -> None:
    """配置日志"""
    log_config: dict[str, Any] = cast(dict[str, Any], config.get("logging", {}))
    log_level_name: str = cast(str, log_config.get("level", "INFO"))
    log_level: int = getattr(logging, log_level_name.upper(), logging.INFO)
    log_dir: str = cast(str, log_config.get("dir", "./logs"))

    os.makedirs(log_dir, exist_ok=True)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                os.path.join(log_dir, f"wechat_agent_{datetime.now().strftime('%Y%m%d')}.log"),
                encoding="utf-8",
            ),
        ],
    )


def cmd_init() -> None:
    """运行 wechat-cli init 初始化"""
    print("=" * 50)
    print("\U0001f511 正在初始化 wechat-cli ...")
    print("   请确保微信桌面版正在运行！")
    print("=" * 50)

    try:
        _ = subprocess.run(
            ["wechat-cli", "init"],
            capture_output=False,
            check=False,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError:
        print("\u274c 未找到 wechat-cli，请先安装:")
        print("   pip install wechat-cli")
        print("   或从源码安装: https://github.com/freestylefly/wechat-cli")
        sys.exit(1)


def cmd_auto_run(config: dict[str, Any], date_str: str) -> str | None:
    """执行一次完整的日报生成"""
    WeChatReader_cls, MessageFilter_cls, Summarizer_cls, ReportGenerator_cls = _import()

    print(f"\n{'='*50}")
    print(f"\U0001f4cb 微信工作日报 - {date_str}")
    print(f"{'='*50}\n")

    # 1. 读取消息
    print("\U0001f4e5 正在读取微信消息...")
    reader = WeChatReader_cls(config)

    try:
        since: datetime = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        since = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    messages = reader.get_messages(since=since)
    print(f"   获取到 {len(messages)} 条消息\n")

    if not messages:
        print("\u2139\ufe0f 没有消息，跳过剩余步骤\n")
        return None

    # 2. 参与度分析
    print("\U0001f50d 正在分析消息参与度...")
    filter_ = MessageFilter_cls(config)
    analysis: dict[str, Any] = cast(dict[str, Any], filter_.analyze(messages))
    my_chats: list[Any] = cast(list[Any], analysis.get("my_chats", []))
    my_count: int = cast(int, analysis.get("my_count", 0))
    unreplied_count: int = cast(int, analysis.get("unreplied_count", 0))
    print(f"   我参与的会话: {len(my_chats)} 个")
    print(f"   我发的消息: {my_count} 条")
    print(f"   未回复的@我: {unreplied_count} 条\n")

    # 3. 生成摘要
    print("\U0001f9e0 正在生成摘要...")
    summarizer = Summarizer_cls(config)
    summary: dict[str, Any] = cast(dict[str, Any], summarizer.summarize(analysis, date_str))
    work_items: list[Any] = cast(list[Any], summary.get("work_items", []))
    work_count: int = len(work_items)
    print(f"   已生成 {work_count} 条工作总结\n")

    # 4. 生成日报
    print("\U0001f4dd 正在生成日报文件...")
    generator = ReportGenerator_cls(config)
    filepath: str = generator.generate(analysis, summary, date_str)
    print(f"\u2705 工作总结已生成: {filepath}")
    detail_path: str = filepath.replace("工作总结_", "对话详情_")
    print(f"\u2705 对话详情已生成: {detail_path}\n")

    # 5. 保存待办JSON和本地HTML
    output_dir: str = os.path.join(os.path.dirname(__file__), "output")
    try:
        from src.todos_sync import save_todos_json, sync_todos_to_local_html
        todos: list[Any] = cast(list[Any], summary.get("todos", []))
        _ = save_todos_json(todos, output_dir)
        _ = sync_todos_to_local_html(todos, date_str, output_dir)
    except Exception as e:
        logging.warning(f"待办同步出错: {e}")



    # 6. 在终端打印摘要
    print("=" * 50)
    print("\U0001f4cc 今日工作总结")
    print("=" * 50)

    if work_items:
        print("\n\U0001f4cb 工作总结:")
        for w in work_items:
            chat: str = cast(str, cast(dict[str, Any], w).get("chat", ""))
            wi_summary: str = cast(str, cast(dict[str, Any], w).get("summary", ""))
            print(f"  \U0001f4cd {chat}")
            print(f"     {wi_summary[:100]}")
            print()

    todos_all: list[Any] = cast(list[Any], summary.get("todos", []))
    if todos_all:
        print("\u2705 待办事项:")
        for t in todos_all:
            td: dict[str, Any] = cast(dict[str, Any], t)
            ctx: str = cast(str, td.get("context", ""))
            ctx_str: str = f" - {ctx[:60]}" if ctx else ""
            print(f"  \u2022 {cast(str, td.get('title', ''))}{ctx_str}")
        print()

    risks: list[Any] = cast(list[Any], summary.get("risk_points", []))
    if risks:
        print("\u26a0\ufe0f 风险识别:")
        for r in risks:
            rd: dict[str, Any] = cast(dict[str, Any], r)
            level: str = cast(str, rd.get("level", "中"))
            risk_text: str = cast(str, rd.get("risk", ""))
            print(f"  [{level}] {risk_text[:80]}")
        print()

    insights: str = cast(str, summary.get("insights", ""))
    if insights:
        print(f"\U0001f4ad 工作感悟: {insights[:120]}")
        print()

    return filepath


def cmd_weekly_report(config: dict[str, Any], date_str: str | None = None) -> str | None:
    """生成周报"""
    WeeklyReportGenerator_cls = _import_weekly()
    generator = WeeklyReportGenerator_cls(config)
    return generator.generate(date_str)


def cmd_watch(config: dict[str, Any], interval_minutes: int = 30) -> None:
    """持续监听模式"""
    logger = logging.getLogger("watch")
    logger.info(f"启动监听模式，每 {interval_minutes} 分钟检查一次")

    while True:
        date_str: str = datetime.now().strftime("%Y-%m-%d")
        try:
            logger.info(f"--- 定时检查: {datetime.now().isoformat()} ---")
            _ = cmd_auto_run(config, date_str)
        except Exception as e:
            logger.error(f"执行出错: {e}", exc_info=True)

        logger.info(f"等待 {interval_minutes} 分钟后再次检查...")
        time.sleep(interval_minutes * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="微信工作日报助手 - 自动读取微信消息，整理工作内容和待办"
    )
    _ = parser.add_argument(
        "--config", "-c", default=None,
        help="配置文件路径（默认: ./config/config.yaml）",
    )
    _ = parser.add_argument(
        "--date", "-d", default=None,
        help="指定日期（格式: YYYY-MM-DD，默认: 今天）",
    )
    _ = parser.add_argument(
        "--init", "-i", action="store_true",
        help="初始化 wechat-cli（提取密钥）",
    )
    _ = parser.add_argument(
        "--watch", "-w", action="store_true",
        help="持续监听模式",
    )
    _ = parser.add_argument(
        "--weekly", "-wk", action="store_true",
        help="生成本周周报",
    )
    _ = parser.add_argument(
        "--interval", "-n", type=int, default=30,
        help="监听间隔（分钟，默认: 30）",
    )

    args: argparse.Namespace = parser.parse_args()

    config_path: str | None = cast(str | None, args.config)
    config: dict[str, Any] = load_config(config_path)
    validate_config(config)
    setup_logging(config)

    # ---- 在线激活检查 ----
    try:
        from license_client import LicenseClient
        from license_client.exceptions import ActivationFailedError, NetworkError

        lc = LicenseClient(
            server_url="https://43.143.121.172",
            product_id="your_app_v1",
            app_name="WeChatWorkAgent",
            verify_ssl=False,
        )
        result = lc.check()
        if not result["valid"]:
            if result["error_code"] == "NOT_ACTIVATED":
                print("")
                print("=" * 50)
                print("  程序未激活")
                print("=" * 50)
                print(f"  本机硬件码: {lc.get_hardware_id()[:16]}...")
                code = input("  请输入激活码 (XXXX-XXXX-XXXX-XXXX): ").strip()
                if not code:
                    print("  未输入激活码，程序退出")
                    sys.exit(1)
                try:
                    act = lc.activate(code.upper())
                    print(f"  激活成功！到期时间: {act['expire_at']}")
                except ActivationFailedError as e:
                    print(f"  激活失败: {e.message} (错误码: {e.code})")
                    sys.exit(1)
                except NetworkError as e:
                    print(f"  网络错误: {e}")
                    sys.exit(1)
            elif result["error_code"] == "HW_MISMATCH":
                print("")
                print("=" * 50)
                print("  机器码已变更")
                print("=" * 50)
                print(f"  {result['reason']}")
                print(f"  当前机器码: {lc.get_hardware_id()}")
                code = input("  请输入新的激活码 (XXXX-XXXX-XXXX-XXXX): ").strip()
                if not code:
                    print("  未输入激活码，程序退出")
                    sys.exit(1)
                try:
                    act = lc.activate(code.upper())
                    print(f"  激活成功！到期时间: {act['expire_at']}")
                except ActivationFailedError as e:
                    print(f"  激活失败: {e.message} (错误码: {e.code})")
                    sys.exit(1)
                except NetworkError as e:
                    print(f"  网络错误: {e}")
                    sys.exit(1)
            elif result["error_code"] == "EXPIRED":
                print(f"License 已过期: {result['reason']}")
                sys.exit(1)
            else:
                print(f"License 验证失败: {result['reason']}")
                sys.exit(1)
        else:
            print(f"License 有效，到期时间: {result.get('expire_at_str', '未知')}")
    except ImportError:
        print("授权模块加载失败，请检查安装是否完整")
        sys.exit(1)
    except Exception as e:
        print(f"授权验证异常: {e}")
        print("建议删除 %APPDATA%\\WeChatWorkAgent\\license.bin 后重新激活")
        sys.exit(1)

    if args.init:
        cmd_init()
    elif args.watch:
        interval: int = cast(int, args.interval)
        cmd_watch(config, interval)
    elif args.weekly:
        weekly_date: str | None = cast(str | None, args.date)
        _ = cmd_weekly_report(config, weekly_date)
    else:
        date_str: str = cast(str, args.date or datetime.now().strftime("%Y-%m-%d"))
        _ = cmd_auto_run(config, date_str)


if __name__ == "__main__":
    main()
