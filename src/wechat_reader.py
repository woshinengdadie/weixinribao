"""
微信聊天记录读取模块
统一使用进程内调用 wechat_cli（inprocess）模式，无需区分开发/打包环境
"""

import json
import re
import sqlite3
import logging
import os
import sys
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional


def _normalize_chat_name(name: str) -> str:
    """规范化会话名用于精确匹配（wechat_reader 与 message_filter 共用）

    规范化: strip + 折叠连续空白 + 移除零宽/不可见字符，避免尾部空格或
    不可见字符导致精确匹配失败（如 "微信支付" vs "微信支付 "）
    """
    if not name:
        return ""
    name = " ".join(name.split())
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cf", "Cc") or ch in "\t\n")
    return name.strip().lower()


def _extract_json(text):
    """从混合文本中提取有效的 JSON 子串

    优先整体 json.loads（绝大多数情况成功），失败再降级到平衡块遍历。
    跳过 [解密]/[载入] 等非 JSON 文本。
    返回第一个可成功 json.loads 的片段，若都不成功则返回 None。
    """
    if not text:
        return None
    # 移除 BOM 和首尾空白
    text = text.strip().lstrip('\ufeff')

    # 优先尝试整体解析（快路径，覆盖 95%+ 场景）
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # 降级：字符级遍历找平衡块（慢路径，处理输出混入额外文本的情况）
    def _find_blocks(s, open_char, close_char):
        """从 s 中提取所有 open_char...close_char 平衡块"""
        blocks = []
        depth = 0
        in_string = False
        escape = False
        block_start = -1

        for i, ch in enumerate(s):
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == open_char:
                if depth == 0:
                    block_start = i
                depth += 1
            elif ch == close_char:
                if depth > 0:
                    depth -= 1
                    if depth == 0 and block_start >= 0:
                        blocks.append(s[block_start:i + 1])
                        block_start = -1
        return blocks

    # 先收集所有 {} 和 [] 平衡块
    candidates = []
    for oc in ("{}", "[]"):
        candidates.extend(_find_blocks(text, oc[0], oc[1]))

    # 按在原文中的位置排序（先出现的先试）
    candidates.sort(key=lambda b: text.index(b) if b in text else 0)

    # 尝试解析，跳过明显不是 JSON 的（如 [解密]、[载入中]）
    for block in candidates:
        # 快速排除：只含中文字符的 [] 块
        if block.startswith("[") and len(block) < 30:
            inner = block[1:-1]
            if re.search(r'[\u4e00-\u9fff]', inner) and not re.search(r'["{}\\d]', inner):
                continue
        try:
            json.loads(block)
            return block
        except json.JSONDecodeError:
            continue

    return None

logger = logging.getLogger(__name__)

# 消息正则预编译（避免每次 history 调用都重新编译）
_MSG_RE_A = re.compile(r'\[([^\]]+)\]\s*(\S+?):\s*(.*)')
_MSG_RE_B = re.compile(r'\[([^\]]+)\]\s+\[(.+?)\]\s*(.*)')


def _get_output_dir() -> str:
    """获取输出目录路径（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 环境：使用 exe 所在目录下的 output
        return os.path.join(os.path.dirname(sys.executable), "output")
    else:
        # 开发环境：使用项目根目录下的 output
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "output")


def _get_project_dir() -> str:
    """获取项目根目录路径"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _filter_recent_sessions(sessions: list, cutoff: datetime) -> list:
    """过滤会话列表，只保留在 cutoff 之后有消息的会话

    会话数据可能包含以下时间字段之一:
    - last_time / last_message_time: 最后消息时间戳
    - time / timestamp: 排序时间戳
    如果没有任何时间字段，只保留前10个（避免大量无时间戳会话拖慢查询）。
    """
    if not sessions:
        return sessions

    recent = []
    no_ts_count = 0
    no_ts_kept = 0
    for s in sessions:
        ts = None
        for key in ("last_time", "last_message_time", "timestamp", "time"):
            raw = s.get(key)
            if raw is None:
                continue
            ts = _parse_session_time(raw)
            if ts:
                break

        if ts is None:
            no_ts_count += 1
            if no_ts_kept < 10:
                recent.append(s)  # 无时间戳的只保留前10个
                no_ts_kept += 1
        elif ts >= cutoff:
            recent.append(s)

    if recent and len(recent) < len(sessions):
        filtered_count = len(sessions) - len(recent) - no_ts_count + (no_ts_count - no_ts_kept)
        logger.info(
            f"会话过滤: {len(sessions)} → {len(recent)} "
            f"(截止 {cutoff.strftime('%m-%d %H:%M')}, 过滤 {filtered_count} 个过期, "
            f"无时间戳 {no_ts_count} 个仅保留 {no_ts_kept} 个)"
        )
    return recent


def _parse_session_time(raw) -> Optional[datetime]:
    """解析会话时间字段（支持多种格式）"""
    if isinstance(raw, (int, float)):
        # Unix 时间戳（秒或毫秒）
        if raw > 1e12:
            raw = raw / 1000
        try:
            return datetime.fromtimestamp(raw)
        except (OSError, ValueError):
            return None
    if isinstance(raw, str):
        raw = raw.strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%m-%d %H:%M:%S",
            "%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(raw, fmt)
                # 无年份格式（如 "%m-%d %H:%M"）默认年份为 1900，修正为当前年份
                if dt.year == 1900 and "%Y" not in fmt:
                    dt = dt.replace(year=datetime.now().year)
                return dt
            except ValueError:
                continue
    return None


class WeChatReader:
    """微信聊天记录读取器"""

    # 微信系统占位符，无需查询聊天记录
    _SYSTEM_PLACEHOLDERS: set = {
        "@placeholder_foldgroup", "brandsessionholder", "brandservicesessionholder",
        "medianote", "appbrand", "appbrandnotify", "filehelper", "floatbottle",
        "newsapp", "qqmail", "qqsync", "weixin",
    }

    def __init__(self, config: dict):
        self._config = config
        self._mode = config.get("mode", "cli")
        self._my_name = config.get("wechat", {}).get("my_name", "")
        self._progress = None  # 进度回调，由 get_messages() 设置
        self._inprocess_lock = threading.Lock()  # 进程内调用串行化，防止 DBCache 被并发破坏

    @staticmethod
    def _check_wechat_cli_available() -> bool:
        """检查 wechat_cli 模块是否可导入"""
        try:
            import wechat_cli  # noqa: F401
            return True
        except ImportError:
            return False

    def test_keys(self) -> dict:
        """测试密钥是否有效：获取会话 → 读取聊天内容 → 返回实际消息证明解密成功

        只做最简单的：找一个会话，读一条聊天记录，验证能不能解密出真实内容。
        不涉及 my_name 匹配、时间过滤等业务逻辑。

        Returns:
            {"ok": bool, "message": str, "session_count": int, "error_detail": str,
             "sample_chat": str, "sample_content": str}
        """
        if not self._check_wechat_cli_available():
            return {"ok": False, "message": "wechat-cli 模块未安装", "session_count": 0,
                    "error_detail": "", "sample_chat": "", "sample_content": ""}

        # 检查密钥文件
        wechat_cli_dir = os.path.expanduser("~/.wechat-cli")
        all_keys = os.path.join(wechat_cli_dir, "all_keys.json")
        if not os.path.exists(all_keys):
            return {
                "ok": False,
                "message": "密钥未配置，请先在「密钥配置」页面填写 passphrase",
                "session_count": 0,
                "error_detail": f"{all_keys} 不存在",
                "sample_chat": "", "sample_content": "",
            }

        # 步骤 1: 获取会话列表，验证能解密 session.db
        output = self._run_wechat_cli_inprocess("sessions", "--format", "json", "--limit", "3")
        if not output:
            return {
                "ok": False,
                "message": "密钥验证失败：无法解密数据库。请检查 passphrase 是否正确，或关闭微信后重新配置密钥",
                "session_count": 0,
                "error_detail": "sessions 命令无输出（数据库解密失败或微信未运行）",
                "sample_chat": "", "sample_content": "",
            }

        try:
            extracted = _extract_json(output)
            if not extracted:
                return {
                    "ok": False, "message": "密钥验证失败：sessions 返回了非 JSON 数据",
                    "session_count": 0, "error_detail": repr(output[:200]),
                    "sample_chat": "", "sample_content": "",
                }
            data = json.loads(extracted)
            if isinstance(data, list):
                sessions = data
            elif isinstance(data, dict):
                sessions = data.get("data", [])
            else:
                sessions = []
        except json.JSONDecodeError as e:
            return {
                "ok": False, "message": "密钥验证失败：JSON 解析异常",
                "session_count": 0, "error_detail": str(e),
                "sample_chat": "", "sample_content": "",
            }

        if not sessions:
            return {
                "ok": True,
                "message": "密钥有效！数据库解密成功，但当前无会话记录（微信可能未登录）",
                "session_count": 0, "error_detail": "",
                "sample_chat": "", "sample_content": "",
            }

        # 步骤 2: 取第一个有效会话（跳过系统占位符），读取聊天内容，验证能解密 message 数据库
        chat_name = ""
        for s in sessions:
            name = (s.get("chat") or s.get("name", "")).strip()
            if self._is_valid_chat(name):
                chat_name = name
                break
        if not chat_name:
            return {
                "ok": True,
                "message": f"密钥有效！解密成功（{len(sessions)} 个会话可读）",
                "session_count": len(sessions), "error_detail": "",
                "sample_chat": "(无可测试的真实会话)", "sample_content": "",
            }

        hist_output = self._run_wechat_cli_inprocess(
            "history", chat_name,
            "--limit", "1",
            "--format", "json",
        )


        if not hist_output:
            return {
                "ok": True,
                "message": f"密钥有效！解密会话列表成功（{len(sessions)} 个会话），但 [{chat_name}] 无聊天记录（该会话可能没有消息数据）",
                "session_count": len(sessions),
                "error_detail": "",
                "sample_chat": chat_name,
                "sample_content": "(无消息)",
            }

        try:
            hist_extracted = _extract_json(hist_output)
            if not hist_extracted:
                return {
                    "ok": True,
                    "message": f"密钥有效！解密成功（{len(sessions)} 个会话）",
                    "session_count": len(sessions),
                    "error_detail": "",
                    "sample_chat": chat_name,
                    "sample_content": "(历史消息格式异常)",
                }
            hist_data = json.loads(hist_extracted)
            msgs = hist_data.get("messages", []) if isinstance(hist_data, dict) else []
            sample = msgs[0] if msgs else "(无消息)"
        except Exception:
            sample = "(解析异常)"

        return {
            "ok": True,
            "message": f"✅ 密钥有效！数据库解密成功，已读取到真实聊天内容",
            "session_count": len(sessions),
            "error_detail": "",
            "sample_chat": chat_name,
            "sample_content": str(sample)[:200],
        }

    def run_wechat_cli(self, *args) -> Optional[str]:
        """公开接口：进程内调用 wechat_cli（带锁保护，线程安全）

        适用于外部模块（如 chat_monitor）需要直接调用 wechat-cli 命令的场景。
        """
        return self._run_wechat_cli_inprocess(*args)

    def _run_wechat_cli_inprocess(self, *args) -> Optional[str]:
        """直接在进程内调用 wechat_cli 模块（统一调用方式，无需子进程）

        相比子进程方式，消除了 EXE 启动 + 重新加载 wechat_cli + 重新解密数据库
        的开销（30s+ → 秒级）。

        注意：wechat_cli 的 DBCache 不是线程安全的，并发调用会破坏缓存，
        导致每次都重新解密数据库。因此使用 _inprocess_lock 串行化调用，
        让首次调用解密缓存后，后续调用直接命中缓存（几秒内完成）。
        """
        import io as _io
        import time as _time
        t0 = _time.time()
        cmd_name = args[0] if args else "?"
        with self._inprocess_lock:
            old_argv = sys.argv
            old_stdout, old_stderr = sys.stdout, sys.stderr
            captured_out = _io.StringIO()
            captured_err = _io.StringIO()
            sys.stdout = captured_out
            sys.stderr = captured_err
            exit_code = 0
            try:
                from wechat_cli.main import cli
                sys.argv = ["wechat_cli"] + list(args)
                cli()
            except SystemExit as e:
                exit_code = e.code if e.code is not None else 0
            except Exception as e:
                logger.error(f"wechat-cli 进程内调用异常: {type(e).__name__}: {e}")
                exit_code = 1
            finally:
                out_text = captured_out.getvalue()
                err_text = captured_err.getvalue()
                sys.stdout = old_stdout
                sys.stderr = old_stderr
                sys.argv = old_argv
            elapsed = _time.time() - t0
            if elapsed > 5:
                logger.info(f"wechat-cli inprocess [{cmd_name}] 耗时 {elapsed:.1f}s")
            if exit_code != 0:
                logger.warning(f"wechat-cli inprocess [{cmd_name}] 退出码={exit_code} (耗时 {elapsed:.1f}s)")
                if err_text.strip():
                    logger.warning(f"  错误: {err_text.strip()[:500]}")
                return None
            return out_text

    def _get_sessions_cli(self) -> List[Dict]:
        """获取会话列表（最多500个，覆盖绝大部分活跃会话）"""
        output = self._run_wechat_cli_inprocess("sessions", "--format", "json", "--limit", "500")

        if not output:
            logger.warning("wechat-cli sessions 命令无输出 — 密钥可能未配置或微信未运行")
            if self._progress:
                self._progress("[警告] 无法获取微信会话列表，请确认：\n"
                               "  1. 微信桌面版正在运行并已登录\n"
                               "  2. 已运行过「初始化密钥」（首次使用必须）")
            return []

        extracted = _extract_json(output)
        if not extracted:
            logger.warning(f"未能从 wechat-cli 输出中提取有效 JSON，原始输出前500字符: {repr(output[:500])}")
            return []
        try:
            data = json.loads(extracted)
        except json.JSONDecodeError as e:
            logger.warning(f"解析会话列表 JSON 失败: {e}")
            logger.warning(f"提取的 JSON 前500: {repr(extracted[:500])}")
            return []

        if isinstance(data, list):
            sessions = data
        elif isinstance(data, dict):
            sessions = data.get("data", []) if isinstance(data.get("data"), list) else []
        else:
            logger.warning(f"会话列表格式异常: 类型={type(data).__name__}")
            return []

        logger.info(f"wechat-cli sessions 返回 {len(sessions)} 个会话")
        # 列出前5个会话名用于排查
        if sessions:
            sample = [s.get("chat", s.get("name", "?")) for s in sessions[:5]]
            logger.info(f"  会话示例: {', '.join(str(x) for x in sample)}")
        return sessions

    def _get_new_messages_cli(self) -> List[Dict]:
        """获取增量新消息"""
        output = self._run_wechat_cli_inprocess("new-messages", "--format", "json")

        if not output:
            return []

        extracted = _extract_json(output)
        if not extracted:
            logger.warning(f"未能从 wechat-cli 新消息输出中提取有效 JSON: {repr(output[:300])}")
            return []
        try:
            data = json.loads(extracted)
        except json.JSONDecodeError as e:
            logger.warning(f"解析新消息 JSON 失败: {e}")
            logger.warning(f"提取的 JSON 前300: {repr(extracted[:300])}")
            return []

        if isinstance(data, dict):
            msgs = data.get("messages", [])
            if isinstance(msgs, list):
                self._save_last_check()
                return msgs

        logger.warning("解析新消息失败")
        return []

    def _get_history_cli(self, chat_name: str, since: datetime, limit: int = 500) -> List[Dict]:
        """获取指定会话的历史消息

        注意: wechat-cli history 返回的 messages 是字符串数组，格式:
          "[2026-06-27 12:30] 发送者: 内容"
          "[2026-06-27 12:31] [图片]"
          "[2026-06-27 12:32] [撤回] 张三 撤回了一条消息"
        """
        since_str = since.strftime("%Y-%m-%d %H:%M:%S")
        output = self._run_wechat_cli_inprocess(
            "history", chat_name,
            "--start-time", since_str,
            "--limit", str(limit),
            "--format", "json",
        )

        if not output:
            if chat_name.startswith("@placeholder_"):
                logger.info(f"跳过微信占位符会话: {chat_name}（其实体聊天已单独获取）")
            else:
                logger.info(f"会话 [{chat_name}] 无消息 (since={since_str}, 可能是该时间范围内无记录或会话名不匹配)")
            return []

        extracted = _extract_json(output)
        if not extracted:
            logger.warning(f"会话 [{chat_name}] JSON提取失败，原始输出: {repr(output[:300])}")
            return []
        try:
            data = json.loads(extracted)
        except json.JSONDecodeError as e:
            logger.warning(f"会话 [{chat_name}] JSON解析失败: {e}，原始: {repr(extracted[:300])}")
            return []

        if isinstance(data, dict):
            raw_msgs = data.get("messages", [])
            if not isinstance(raw_msgs, list):
                raw_msgs = []
            wechat_count = data.get("count", len(raw_msgs))
            chat = data.get("chat", chat_name)
            is_group = data.get("is_group", "@chatroom" in data.get("username", chat_name))
            logger.info(f"会话 [{chat_name}] 返回 {wechat_count} 条（{'群聊' if is_group else '私聊'}）")
        else:
            raw_msgs = []
            wechat_count = 0
            chat = chat_name
            is_group = "@chatroom" in chat_name

        # 消息解析：模块级预编译正则
        parsed = []
        skipped = 0
        for raw_msg in raw_msgs:
            if isinstance(raw_msg, dict):
                if "is_group" not in raw_msg:
                    raw_msg["is_group"] = is_group
                parsed.append(raw_msg)
                continue

            if isinstance(raw_msg, str) and raw_msg.strip():
                # 快速跳过明显不匹配的消息（不以 [ 开头）
                if raw_msg[0] != '[':
                    skipped += 1
                    parsed.append({"time": "", "sender": "", "content": raw_msg.strip(), "chat": chat, "is_group": is_group})
                    continue

                # 尝试格式A: sender: content
                m = _MSG_RE_A.match(raw_msg)
                if m:
                    time_str = m.group(1)
                    sender = m.group(2) or ""
                    content = m.group(3) or ""

                    # 时间标准化
                    time_sort = time_str
                    now = datetime.now()
                    if len(time_str) == 16 and "-" in time_str and time_str[2] == "-":
                        time_sort = f"{now.year}-{time_str}"
                    elif len(time_str) == 5 and ":" in time_str:
                        time_sort = f"{now.strftime('%Y-%m-%d')} {time_str}"

                    parsed.append({
                        "time": time_str,
                        "time_sort": time_sort,
                        "sender": sender,
                        "content": content.strip(),
                        "chat": chat,
                        "is_group": is_group,
                    })
                    continue

                # 尝试格式B: [类型] 无发送者（图片/视频/撤回/系统消息）
                m = _MSG_RE_B.match(raw_msg)
                if m:
                    time_str = m.group(1)
                    msg_type = m.group(2)
                    extra = m.group(3)
                    content = f"[{msg_type}]"
                    if extra:
                        content += f" {extra.strip()}"

                    time_sort = time_str
                    now = datetime.now()
                    if len(time_str) == 16 and "-" in time_str and time_str[2] == "-":
                        time_sort = f"{now.year}-{time_str}"
                    elif len(time_str) == 5 and ":" in time_str:
                        time_sort = f"{now.strftime('%Y-%m-%d')} {time_str}"

                    parsed.append({
                        "time": time_str,
                        "time_sort": time_sort,
                        "sender": "",
                        "content": content,
                        "chat": chat,
                        "is_group": is_group,
                    })
                    continue

                # 无法解析的消息，保留原始内容
                skipped += 1
                parsed.append({
                    "time": "",
                    "sender": "",
                    "content": raw_msg.strip(),
                    "chat": chat,
                    "is_group": is_group,
                })

        if skipped:
            logger.info(f"会话 [{chat_name}] 有 {skipped}/{wechat_count} 条消息格式无法识别（已保留原始内容）")

        # 检查是否可能因 limit 不足而截断
        if wechat_count >= limit:
            logger.warning(
                f"会话 [{chat_name}] 返回消息数已达到 limit={limit}，"
                f"如果该会话消息较多，可能有遗漏。建议减少时间范围或增加 limit。"
            )

        return parsed

    def _normalize_cli_message(self, msg: dict, chat_name: str = "") -> Dict:
        """统一化消息格式"""
        return {
            "chat": msg.get("chat", chat_name),
            "sender": msg.get("sender", msg.get("display_name", "")),
            "content": msg.get("content", msg.get("message", msg.get("last_message", ""))),
            "time": msg.get("time", msg.get("time_sort", "")),
            "time_sort": msg.get("time_sort", msg.get("time", "")),
            "is_group": msg.get("is_group", False),
            "msg_type": msg.get("msg_type", ""),
        }

    def _load_cached_chat_names(self) -> set:
        """加载缓存的会话名列表（补充 sessions 遗漏的会话）

        缓存只存展示名，无法存 username。因此加载时不调用 _is_valid_chat，
        而是在补充到 chat_names 后由后续流程自然过滤。
        """
        cache_file = os.path.join(_get_output_dir(), ".chat_cache.json")
        try:
            if os.path.exists(cache_file):
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return set(data.get("chats", []))
        except Exception:
            pass
        return set()

    def _save_cached_chat_names(self, names: set):
        """保存会话名缓存"""
        output_dir = _get_output_dir()
        os.makedirs(output_dir, exist_ok=True)
        cache_file = os.path.join(output_dir, ".chat_cache.json")
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"chats": list(names)}, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"保存会话缓存失败: {e}")

    def _ensure_last_check_file(self):
        """确保 last_check 文件存在"""
        last_check_file = os.path.join(_get_output_dir(), ".last_check.json")
        if not os.path.exists(last_check_file):
            self._save_last_check(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))

    def _load_last_check(self) -> datetime:
        """加载上次检查时间"""
        last_check_file = os.path.join(_get_output_dir(), ".last_check.json")
        try:
            if os.path.exists(last_check_file):
                with open(last_check_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return datetime.fromisoformat(data.get("last_check", ""))
        except Exception:
            pass
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    def _save_last_check(self, dt: Optional[datetime] = None):
        """保存检查时间"""
        if dt is None:
            dt = datetime.now()
        last_check_file = os.path.join(_get_output_dir(), ".last_check.json")
        os.makedirs(os.path.dirname(last_check_file), exist_ok=True)
        try:
            with open(last_check_file, "w", encoding="utf-8") as f:
                json.dump({"last_check": dt.isoformat()}, f)
        except Exception as e:
            logger.warning(f"保存检查时间失败: {e}")

    def _is_valid_chat(self, name: str, username: str = "") -> bool:
        """判断会话名是否可查询（非系统占位符、非公众号）

        公众号的 display_name 可能是"CAD自学网"，但 username 是 gh_xxx，
        需要同时检查 username 来过滤。
        """
        if not name or not name.strip():
            return False
        name_lower = name.strip().lower()
        if name_lower in self._SYSTEM_PLACEHOLDERS:
            return False
        # 检查展示名是否以 gh_ 开头
        if name_lower.startswith("gh_"):
            return False
        # 检查 username 是否以 gh_ 开头（公众号的真实标识）
        if username and username.strip().lower().startswith("gh_"):
            return False
        return True

    def _fetch_via_cli(self, since: datetime, progress=None) -> List[Dict]:
        """通过 CLI 获取消息（并行查询所有会话历史）"""
        # 1. 获取活跃会话列表
        sessions = self._get_sessions_cli()

        # 2. 按最后消息时间过滤：只看 since 之后有消息的会话
        recent_sessions = _filter_recent_sessions(sessions, cutoff=since)

        # 3. 收集有效会话名称（同时检查 username 过滤公众号）
        all_chat_names: set = set()
        for session in sessions:
            name = (session.get("chat") or session.get("name", "")).strip()
            uname = session.get("username", "")
            if self._is_valid_chat(name, uname):
                all_chat_names.add(name)

        chat_names: set = set()
        for session in recent_sessions:
            name = (session.get("chat") or session.get("name", "")).strip()
            uname = session.get("username", "")
            if self._is_valid_chat(name, uname):
                chat_names.add(name)

        logger.info(
            f"时间过滤: {len(sessions)} → {len(recent_sessions)} 个会话 "
            f"(截止 {since.strftime('%m-%d %H:%M')})"
        )
        logger.info(f"有效会话: 筛选后 {len(chat_names)} 个 / 全部 {len(all_chat_names)} 个")

        # 4. 如果过滤后太少，补充缓存的会话名
        if len(chat_names) < 20:
            cached_names = self._load_cached_chat_names()
            if cached_names:
                logger.info(f"补充缓存会话: {len(cached_names)} 个")
            chat_names.update(cached_names)

        # 5. 过滤排除列表（精确匹配会话名称，匹配前规范化两侧名称）
        #    使用模块级 _normalize_chat_name（与 message_filter 共用）
        #    注意：补充缓存后必须重新过滤屏蔽列表，因为用户可能已修改屏蔽配置
        exclude = self._config.get("monitor", {}).get("exclude_chats", [])
        exclude_normalized = {_normalize_chat_name(ex) for ex in exclude if ex and ex.strip()}
        filtered_chats: List[str] = []
        for name in chat_names:
            if _normalize_chat_name(name) not in exclude_normalized:
                filtered_chats.append(name)
        if exclude_normalized and len(chat_names) > len(filtered_chats):
            blocked = len(chat_names) - len(filtered_chats)
            logger.info(f"屏蔽列表过滤: {blocked} 个会话被精确屏蔽")

        total_chats = len(filtered_chats)
        all_chats = sum(1 for s in sessions if self._is_valid_chat(s.get("chat") or s.get("name", ""), s.get("username", "")))
        logger.info(f"活跃会话: {total_chats} 个 (全部 {all_chats} 个，已过滤非近期 + 排除列表)")

        if progress:
            progress(f"会话列表获取完成: {total_chats} 个可查询会话，并行查询中...")

        # 预热：预先做一次 history 调用，触发数据库解密缓存
        # 这样后续所有 history 调用直接命中缓存，速度快 20-50 倍
        if filtered_chats:
            warm_chat = filtered_chats[0]
            logger.info(f"预热 DBCache: 使用 [{warm_chat}] 预解密数据库...")
            self._get_history_cli(warm_chat, since, 1)
            logger.info("预热完成，后续查询将命中缓存")

        # 6. 获取各会话历史消息（多线程）
        #    wechat_cli 调用受 _inprocess_lock 串行化（DBCache 安全），
        #    但消息解析（正则、时间标准化）无锁竞争，可多线程并行提升 CPU 利用率
        known_names: set = set(filtered_chats)
        success_count = 0
        empty_chats: list[str] = []  # 收集无消息的会话名用于诊断
        messages: List[Dict] = []
        msg_lock = threading.Lock()

        workers = max(1, os.cpu_count() or 4)
        logger.info(f"多线程查询: total_chats={total_chats}, workers={workers}（wechat_cli 串行，解析并行）")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(self._get_history_cli, chat_name, since, 500): chat_name
                for chat_name in filtered_chats
            }

            processed = 0
            for future in as_completed(future_map):
                chat_name = future_map[future]
                processed += 1
                try:
                    history = future.result(timeout=60)
                except Exception as e:
                    logger.warning(f"会话 [{chat_name}] 查询异常: {type(e).__name__}: {e}")
                    with msg_lock:
                        known_names.discard(chat_name)
                        empty_chats.append(f"{chat_name}(异常:{e})")
                    continue

                if history:
                    with msg_lock:
                        success_count += 1
                        for hist_msg in history:
                            messages.append(self._normalize_cli_message(hist_msg, chat_name))
                else:
                    with msg_lock:
                        known_names.discard(chat_name)
                        empty_chats.append(chat_name)

                if progress and (processed % 20 == 0 or processed == total_chats):
                    progress(f"已处理 {processed}/{total_chats} 个会话，{success_count} 个有消息")

        # 诊断：汇总无消息会话
        if empty_chats and len(empty_chats) <= 30:
            logger.info(f"无消息会话 ({len(empty_chats)} 个): {', '.join(empty_chats)}")
        elif empty_chats:
            logger.info(f"无消息会话 ({len(empty_chats)} 个)，过多不逐一列出")

        # 7. 保存更新后的缓存
        self._save_cached_chat_names(known_names)
        seen: set = set()
        unique_messages: List[Dict] = []
        for msg in sorted(messages, key=lambda m: m.get("time_sort", m.get("time", ""))):
            dedup_key = (
                f"{msg.get('chat', '')}_"
                f"{msg.get('time_sort', msg.get('time', ''))}_"
                f"{msg.get('content', '')[:50]}"
            )
            if dedup_key not in seen:
                seen.add(dedup_key)
                unique_messages.append(msg)

        logger.info(f"共获取到 {len(unique_messages)} 条消息（来自 {success_count}/{total_chats} 个会话）")
        return unique_messages

    def _fetch_via_db(self, since: datetime) -> List[Dict]:
        """通过数据库直接获取消息"""
        db_dir = self._config.get("wechat", {}).get("db_dir", "")
        if not db_dir:
            logger.error("未配置 db_dir，无法通过数据库模式读取")
            return []
        return self._get_messages_db(db_dir, since)

    def _get_db_connection(self, db_path: str) -> Optional[sqlite3.Connection]:
        """连接并解密微信数据库"""
        db_key = self._config.get("wechat", {}).get("db_key", "")
        if not db_key:
            logger.error("未配置 db_key")
            return None

        try:
            conn = sqlite3.connect(db_path)
            # 16进制 key转换为bytes
            key_bytes = bytes.fromhex(db_key)
            conn.execute(f"PRAGMA key=\"x'{key_bytes.hex()}'\";")
            conn.execute("PRAGMA cipher_compatibility = 3;")
            conn.execute("SELECT count(*) FROM sqlite_master;")
            return conn
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")
            return None

    def _get_messages_db(self, db_dir: str, since: datetime) -> List[Dict]:
        """从数据库直接读取消息"""
        messages = []
        # 查找所有 .db 文件
        db_files = []
        for root, dirs, files in os.walk(db_dir):
            for f in files:
                if f.endswith(".db") and "message" in f.lower():
                    db_files.append(os.path.join(root, f))

        for db_path in db_files:
            conn = self._get_db_connection(db_path)
            if not conn:
                continue

            try:
                cursor = conn.execute("""
                    SELECT CreateTime, StrContent, Type, IsSender
                    FROM MSG
                    WHERE CreateTime > ?
                    ORDER BY CreateTime ASC
                """, (int(since.timestamp()),))
                for row in cursor:
                    create_time, content, msg_type, is_sender = row
                    messages.append({
                        "time": datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S"),
                        "content": content or "",
                        "msg_type": msg_type,
                        "is_sender": bool(is_sender),
                        "chat": os.path.basename(db_path),
                    })
            except Exception as e:
                logger.warning(f"读取数据库 {db_path} 失败: {e}")
            finally:
                conn.close()

        return messages

    def get_messages(self, since: Optional[datetime] = None, progress=None) -> List[Dict]:
        """获取消息的入口
        根据配置自动选择 CLI 或 数据库 模式

        Args:
            since: 从此时间开始获取，None 则从今天0点开始
            progress: 进度回调 progress(msg)

        Returns:
            消息列表，每条含: chat, sender, content, time, time_sort, is_group
        """
        if since is None:
            since = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        self._progress = progress  # 存储进度回调供内部方法使用
        self._ensure_last_check_file()

        if self._mode == "direct":
            return self._fetch_via_db(since)
        else:
            return self._fetch_via_cli(since, progress=progress)
