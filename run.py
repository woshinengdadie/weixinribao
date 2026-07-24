"""
微信工作日报助手 - 桌面应用入口
使用 PyWebView 创建原生窗口（非浏览器）
失败时 fallback 到浏览器打开

命令行参数:
  --init      初始化微信密钥（弹窗显示结果，完成后退出）
"""
# pyright: reportConstantRedefinition=false, reportMissingImports=false, reportMissingTypeArgument=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportUnusedCallResult=false, reportUnusedVariable=false, reportUnusedImport=false
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportDeprecated=false, reportUnreachable=false, reportMissingTypeStubs=false

__version__ = "2.2.0.1"  # Auto-updated by build script

import io
import os
import sys
import time
import logging
import threading
import urllib.request

# ===== 窗口化兼容：重定向 stdout/stderr，防止 print 崩溃 =====
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

# 确保项目目录和 src 在路径中
if getattr(sys, "frozen", False):
    PROJECT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(PROJECT_DIR, "src")
for p in [PROJECT_DIR, SRC_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

# 日志：只写文件，不输出到控制台（窗口化后没有控制台）
log_dir = os.path.join(PROJECT_DIR, "logs")
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(log_dir, f"agent_{time.strftime('%Y%m%d')}.log"),
            encoding="utf-8",
        ),
    ],
)

# 关闭第三方库的 debug 噪音
for lib in ("flask", "werkzeug", "webview", "urllib3", "openai"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("run")


def _show_info(title: str, message: str):
    """弹窗显示信息（窗口化模式下替代 print）"""
    try:
        import tkinter.messagebox as msgbox
        msgbox.showinfo(title, message)
    except Exception as e:
        logger.warning(f"弹窗失败（可能是无头环境），降级到控制台: {e}")
        print(f"[{title}] {message}")


def _show_error(title: str, message: str):
    """弹窗显示错误"""
    try:
        import tkinter.messagebox as msgbox
        msgbox.showerror(title, message)
    except Exception as e:
        logger.warning(f"错误弹窗失败（可能是无头环境），降级到控制台: {e}")
        print(f"[{title}] {message}")


def _find_wechat_db_dirs() -> list:
    """自动搜索本机微信数据库目录，返回候选路径列表"""
    import glob as _glob
    candidates = []
    userprofile = os.environ.get("USERPROFILE", "")
    base_dirs = []

    # 常见微信数据根目录
    if userprofile:
        base_dirs += [
            os.path.join(userprofile, "Documents", "WeChat Files"),
            os.path.join(userprofile, "Documents", "xwechat_files"),
        ]
    # 其他盘符
    for drive in ("D:", "E:", "C:"):
        for sub in ("WeChat Files", "xwechat_files"):
            base_dirs.append(os.path.join(drive, sub))

    for base in base_dirs:
        if not os.path.isdir(base):
            continue
        try:
            for entry in os.listdir(base):
                # 微信账号目录名格式: wxid_xxx 或 纯字母数字
                full = os.path.join(base, entry)
                if not os.path.isdir(full):
                    continue
                # 查找 Msg 或 msg 子目录
                for msg_name in ("Msg", "msg", "db_storage"):
                    msg_dir = os.path.join(full, msg_name)
                    if os.path.isdir(msg_dir):
                        candidates.append(msg_dir)
                # 也可能直接就是 db_storage
                db = os.path.join(full, "db_storage")
                if os.path.isdir(db):
                    candidates.append(db)
        except PermissionError:
            continue

    # 去重
    seen = set()
    unique = []
    for c in candidates:
        norm = os.path.normpath(c).lower()
        if norm not in seen:
            seen.add(norm)
            unique.append(c)
    return unique


def _check_wechat_cli_keys() -> bool:
    """检查 ~/.wechat-cli/accounts/ 下是否已有密钥文件"""
    base = os.path.expanduser("~/.wechat-cli/accounts")
    if not os.path.isdir(base):
        return False
    for entry in os.listdir(base):
        account_dir = os.path.join(base, entry)
        if os.path.isdir(account_dir):
            keys_file = os.path.join(account_dir, "keys.json")
            if os.path.exists(keys_file) and os.path.getsize(keys_file) > 0:
                return True
    return False


def _try_init_with_args(extra_args: list) -> tuple:
    """执行一次 wechat-cli init，返回 (success, output_text)"""
    import subprocess
    if getattr(sys, "frozen", False):
        try:
            import wechat_cli.main
            import wechat_cli.commands.init
            sys.argv = ["wechat_cli", "init"] + extra_args
            old_stdout, old_stderr = sys.stdout, sys.stderr
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            try:
                wechat_cli.main.cli()
                out = sys.stdout.getvalue() + sys.stderr.getvalue()
                return True, out
            except SystemExit as e:
                out = sys.stdout.getvalue() + sys.stderr.getvalue()
                return (e.code is None or e.code == 0), out
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr
        except ImportError:
            return False, "wechat-cli 模块未找到"
        except Exception as e:
            return False, str(e)
    else:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "wechat_cli", "init"] + extra_args,
                check=False, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            out = result.stdout + result.stderr
            return result.returncode == 0, out
        except Exception as e:
            return False, str(e)


def _run_wechat_init(extra_args: "list[str] | None" = None):
    """运行 wechat-cli init 初始化微信密钥，自动搜索数据库路径，结果用弹窗显示"""
    if extra_args is None:
        extra_args = []

    logger.info("开始初始化微信密钥...")

    all_outputs = []

    # 第一步：尝试默认方式（不指定路径）
    if not extra_args:
        logger.info("尝试默认方式初始化...")
        success, out = _try_init_with_args([])
        all_outputs.append(("[默认]", out))
        if success:
            logger.info("默认方式成功")
            _show_info("初始化成功", "微信密钥提取完成！\n\n现在可以启动程序开始使用了。")
            return

    # 第二步：如果指定了 db_dir 参数，直接尝试
    if extra_args:
        logger.info(f"尝试指定参数: {extra_args}")
        success, out = _try_init_with_args(extra_args)
        all_outputs.append((f"[参数: {extra_args}]", out))
        if success:
            logger.info("指定参数成功")
            _show_info("初始化成功", "微信密钥提取完成！\n\n现在可以启动程序开始使用了。")
            return

    # 第三步：自动搜索微信数据库目录
    logger.info("自动搜索微信数据库目录...")
    db_dirs = _find_wechat_db_dirs()
    logger.info(f"找到 {len(db_dirs)} 个候选数据库目录: {db_dirs}")

    if db_dirs:
        for db_dir in db_dirs:
            logger.info(f"尝试 --db-dir {db_dir}")
            success, out = _try_init_with_args(["--db-dir", db_dir])
            all_outputs.append((f"[--db-dir {db_dir}]", out))
            if success:
                logger.info(f"成功: --db-dir {db_dir}")
                _show_info(
                    "初始化成功",
                    f"微信密钥提取完成！\n数据库路径: {db_dir}\n\n现在可以启动程序开始使用了。"
                )
                return
    else:
        logger.warning("未找到任何微信数据库候选目录")

    # 全部失败
    last_output = all_outputs[-1][1] if all_outputs else "无输出"

    # 兼容微信 4.1.x：wechat-cli 可能成功提取密钥但数据库路径校验报 MISSING
    # 检查 ~/.wechat-cli 是否已有密钥文件
    keys_exist = _check_wechat_cli_keys()
    if keys_exist:
        logger.info("检测到 wechat-cli 密钥已存在（可能校验失败但密钥提取成功）")
        _show_info(
            "初始化成功",
            "微信密钥已提取完成！\n（数据库路径校验有警告，但密钥已就绪，不影响使用）\n\n现在可以启动程序开始使用了。"
        )
        return

    logger.error(f"所有方式均失败，最后输出: {last_output[-500:]}")
    tried_info = "\n".join(f"    {label}" for label, _ in all_outputs)

    msg = (
        "密钥提取失败，已尝试以下方式：\n"
        f"{tried_info}\n\n"
        "请尝试以下方法之一：\n\n"
        "【方法1】以管理员身份运行本程序后重试\n\n"
        "【方法2】用 wx_key 手动配置（微信 4.1+ 推荐）：\n"
        "  1. 下载 wx_key → 管理员运行 → 获取密钥\n"
        "  2. 打开程序界面 → 基本设置 → wx_key 区域\n"
        "  3. 填入 passphrase + wxid → 点击「配置密钥」\n\n"
        "【方法3】手动指定数据库目录：\n"
        "  初始化密钥.bat --db-dir \"你的数据库路径\"\n\n"
        "详细信息已写入 logs/ 目录"
    )
    _show_error("初始化失败", msg)


def _try_forward_wechat_cli() -> bool:
    """在冻结环境中拦截 -m wechat_cli 参数，直接运行 wechat_cli CLI 后退出

    wechat_reader._run_wechat_cli 在 PyInstaller 打包版中会通过 subprocess 调用
    sys.executable -m wechat_cli <args>。由于 sys.executable 是本 EXE，
    正常启动会进入 GUI 主流程而非执行 wechat_cli。此函数拦截该调用路径，
    在进程内直接运行 wechat_cli.main.cli()，输出写入 OS 标准输出供父进程读取。

    关键：--windowed 打包下 sys.stdout 为 None，模块级已替换为 StringIO。
    我们需要绕过 sys.stdout，直接写 OS 文件描述符，确保 subprocess.run(capture_output=True)
    能从父进程捕获到输出。

    Returns:
        True 表示已拦截并处理（调用方应直接 return），False 表示未匹配。
    """
    args = sys.argv
    # 匹配: WeChatWorkAgent.exe -m wechat_cli <后续参数...>
    if len(args) < 3:
        return False
    if args[1] != "-m":
        return False
    if args[2] not in ("wechat_cli", "wechat-cli"):
        return False

    # 设置 sys.argv 为 wechat_cli 期望的格式
    sys.argv = ["wechat_cli"] + args[3:]

    # 使用独立的 StringIO 捕获输出，避开模块级可能替换的 sys.stdout
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = captured_out
    sys.stderr = captured_err

    exit_code = 0
    try:
        from wechat_cli.main import cli
        cli()
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0
    except Exception as e:
        logger.exception("wechat_cli 模块调用失败")
        print(f"wechat_cli 错误: {e}", file=captured_err)
        exit_code = 1
    finally:
        out_text = captured_out.getvalue()
        err_text = captured_err.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # 将捕获的输出写入 OS 标准输出（绕过 sys.stdout 可能为 None 的问题）
    if out_text:
        try:
            os.write(1, out_text.encode("utf-8", errors="replace"))
        except (OSError, AttributeError, ValueError):
            pass
    if err_text:
        try:
            os.write(2, err_text.encode("utf-8", errors="replace"))
        except (OSError, AttributeError, ValueError):
            pass

    sys.exit(exit_code)
    return True  # unreachable but keeps type checker happy


def _wait_for_flask(url: str = "http://127.0.0.1:5566", timeout: int = 15) -> bool:
    """等待 Flask 服务就绪"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


class JsApi:
    """JavaScript 可调用的 PyWebView 原生 API"""

    def __init__(self):
        self._window = None  # 由 set_window() 注入 webview.Window 实例

    def set_window(self, window) -> None:
        """注入 webview 窗口引用，供窗口控制方法使用"""
        self._window = window

    def minimize_window(self):
        try:
            if self._window:
                self._window.minimize()
        except Exception as e:
            logger.error(f"最小化窗口失败: {e}")

    def toggle_maximize(self):
        """切换最大化状态：frameless 模式下没有 maximize 按钮，用 toggle_fullscreen 替代"""
        try:
            if self._window:
                # pywebview frameless 没有原生 maximize，用 fullscreen 切换
                self._window.toggle_fullscreen()
        except Exception as e:
            logger.error(f"切换最大化失败: {e}")

    def close_window(self):
        try:
            if self._window:
                self._window.destroy()
        except Exception as e:
            logger.error(f"关闭窗口失败: {e}")

    def pick_folder(self):
        root = None
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            folder = filedialog.askdirectory(title="选择文件夹")
            return folder or ""
        except Exception as e:
            logger.error(f"文件夹选择失败: {e}")
            return ""
        finally:
            if root:
                try:
                    root.destroy()
                except Exception:
                    pass


def start_flask():
    """启动 Flask 服务（关闭 debug 模式）"""
    from app.server import create_app
    app = create_app()
    logger.info("Flask 服务启动于 http://127.0.0.1:5566")
    app.run(host="127.0.0.1", port=5566, debug=False, use_reloader=False)


def _start_browser_fallback():
    """PyWebView 不可用时，自动用浏览器打开"""
    import webbrowser
    url = "http://127.0.0.1:5566"
    logger.warning("PyWebView 不可用，回退到浏览器模式")
    webbrowser.open(url)
    _show_info(
        "微信工作日报助手",
        f"桌面窗口组件不可用，已自动打开浏览器。\n\n请访问: {url}\n\n关闭浏览器后程序将退出。"
    )


def _parse_version(ver_str: str) -> tuple:
    """将版本号字符串转为可比较的整数元组，如 '2.0.1.15' → (2,0,1,15)"""
    try:
        return tuple(int(x) for x in ver_str.strip().split("."))
    except Exception:
        return (0,)


UPDATE_URL = "https://raw.githubusercontent.com/woshinengdadie/weixinribao/main/updates.json"


def check_for_updates(silent: bool = True) -> dict | None:
    """检查是否有新版本可用

    Args:
        silent: True 时仅在发现新版本时弹窗；False 时无论有无更新都弹窗（手动检查）

    Returns:
        有新版时返回服务器版本信息 dict，否则返回 None
    """
    try:
        req = urllib.request.Request(UPDATE_URL)
        req.add_header("User-Agent", f"WeChatWorkAgent/{__version__}")
        with urllib.request.urlopen(req, timeout=8) as resp:
            import json
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"检查更新失败: {e}")
        if not silent:
            _show_info("检查更新", f"无法连接到更新服务器\n\n{str(e)[:200]}")
        return None

    remote_ver = data.get("version", "")
    local_tuple = _parse_version(__version__)
    remote_tuple = _parse_version(remote_ver)

    if remote_tuple <= local_tuple:
        if not silent:
            _show_info("检查更新", f"当前已是最新版本 v{__version__}")
        return None

    # 有新版本
    notes = data.get("notes", "无更新说明")
    dl_url = data.get("url", "")
    date = data.get("date", "")
    msg = (
        f"发现新版本 v{remote_ver}（当前 v{__version__}）\n"
        f"发布日期: {date}\n\n"
        f"更新内容:\n{notes}\n\n"
        f"下载地址:\n{dl_url}\n\n"
        f"是否现在打开下载页面？"
    )
    _show_info("发现新版本", msg)
    # 尝试打开下载页
    if dl_url:
        try:
            import webbrowser
            webbrowser.open(dl_url)
        except Exception:
            pass
    return data


def _show_activation_dialog(lc):
    """弹出激活码输入框"""
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    msg = "程序未激活，请输入激活码\n\n格式: XXXX-XXXX-XXXX-XXXX\n\n（激活码请联系管理员获取）"
    code = simpledialog.askstring("激活 License", msg, parent=root)
    root.destroy()
    if not code:
        _show_error("未激活", "未输入激活码，程序将退出")
        sys.exit(1)
    try:
        from license_client.exceptions import ActivationFailedError, NetworkError
        result = lc.activate(code.strip().upper())
        if result["ok"]:
            _show_info("激活成功", "激活成功！\n\n到期时间: " + result["expire_at"])
        else:
            _show_error("激活失败", "激活失败: " + result.get("error", "未知错误"))
            sys.exit(1)
    except ActivationFailedError as e:
        _show_error("激活失败", e.message + "\n\n错误码: " + e.code)
        sys.exit(1)
    except NetworkError as e:
        _show_error("网络错误", "无法连接激活服务器\n\n" + str(e) + "\n\n请检查网络连接")
        sys.exit(1)





def main():
    # ---- 打包环境子进程模式：处理 -m wechat_cli 转发 ----
    # 当 wechat_reader 在冻结模式下通过 subprocess 调用
    #   WeChatWorkAgent.exe -m wechat_cli <args>
    # 时，我们需要在这里拦截并直接运行 wechat_cli 模块，然后退出。
    _wechat_cli_handled = _try_forward_wechat_cli()
    if _wechat_cli_handled:
        return

    # 处理命令行参数
    if "--init" in sys.argv:
        idx = sys.argv.index("--init")
        extra = sys.argv[idx + 1:]
        _run_wechat_init(extra)
        return

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
                logger.warning("未激活，等待用户输入激活码")
                _show_activation_dialog(lc)
            elif result["error_code"] == "HW_MISMATCH":
                _show_error("机器码变更", result["reason"] + "\n\n当前机器码: " + lc.get_hardware_id())
                logger.warning("机器码不匹配，重新激活")
                _show_activation_dialog(lc)
            elif result["error_code"] == "EXPIRED":
                _show_error("授权过期", "License 已过期，请联系管理员续期")
                sys.exit(1)
            else:
                _show_error("验证失败", f"License 验证失败: {result['reason']}")
                sys.exit(1)
        else:
            days_left = (int(result.get("expire_at", 0)) - int(__import__('time').time())) // 86400
            if days_left < 30:
                _show_info("授权提醒", f"授权即将到期（剩余 {days_left} 天），请及时续期")
    except ImportError:
        logger.warning("授权模块未加载")
        _show_error("错误", "授权模块加载失败，请检查安装是否完整")
        sys.exit(1)
    except Exception as e:
        logger.error(f"授权检查异常: {e}")
        _show_error("错误", f"授权检查失败: {e}")
        sys.exit(1)


    # 1. 启动 Flask 后端
    logger.info("启动 Flask 后端...")
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # 2. 等待 Flask 就绪
    if not _wait_for_flask():
        logger.error("Flask 启动超时")
        _show_error("启动失败", "Flask 后端启动超时，请检查依赖是否完整。\n查看 logs/ 目录获取详细信息。")
        sys.exit(1)

    # 2.5 后台检查更新（不阻塞启动流程，静默模式）
    threading.Thread(target=check_for_updates, args=(True,), daemon=True).start()

    # 3. 启动 PyWebView 桌面窗口
    try:
        import webview

        # 获取版本号用于窗口标题（兼容打包后）
        ver_str = ""
        try:
            # PyInstaller 打包后文件在 sys._MEIPASS，调试时在项目根目录
            _base = sys._MEIPASS if getattr(sys, '_MEIPASS', None) else os.path.dirname(__file__) if '__file__' in dir() else '.'
            _ver_path = os.path.join(_base, "VERSION")
            if os.path.exists(_ver_path):
                with open(_ver_path, encoding="utf-8") as _vf:
                    _raw = _vf.read().strip()
                _parts = _raw.split(".")
                while _parts and _parts[-1] == "0" and len(_parts) > 3:
                    _parts.pop()
                ver_str = "v" + ".".join(_parts)
        except Exception:
            pass
        if not ver_str:
            ver_str = f"v{__version__}" if __version__ else ""

        title = f"微信工作日报助手 {ver_str}" if ver_str else "微信工作日报助手"
        logger.info(f"启动 PyWebView 桌面窗口（{title}）...")
        # 加时间戳防止缓存
        url = f"http://127.0.0.1:5566?_t={int(time.time())}"
        js_api = JsApi()
        window = webview.create_window(
            title=title,
            url=url,
            width=1000,
            height=720,
            min_size=(800, 550),
            resizable=True,
            frameless=True,           # 隐藏原生标题栏，使用 HTML 自定义标题栏
            easy_drag=True,           # 让 -webkit-app-region: drag 生效（用户可拖拽自定义标题栏）
            js_api=js_api,
        )
        js_api.set_window(window)    # 注入窗口引用，供最小化/最大化使用
        webview.start(debug=False, http_server=False, gui="edgechromium")
    except ImportError:
        _start_browser_fallback()
    except Exception:
        logger.exception("PyWebView 启动失败")
        _start_browser_fallback()


if __name__ == "__main__":
    main()
