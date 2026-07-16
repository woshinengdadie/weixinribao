# pyright: reportImplicitStringConcatenation=false
"""
打包脚本 - 将桌面应用打包为可分发的独立 EXE（无需 Python 环境）
用法: python build_app.py
     或者在虚拟环境中: venv\\Scripts\\python.exe build_app.py
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

# 添加 tools/ 到 sys.path 以导入 version 模块
sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import version as ver_mod

PROJECT = Path(__file__).resolve().parent
DIST_DIR = PROJECT / "dist" / "WeChatWorkAgent"


def run(cmd: list[str], desc: str = "") -> bool:
    """运行命令，打印输出，返回是否成功"""
    if desc:
        print(f"  [{desc}]", end=" ", flush=True)
    result = subprocess.run(cmd, cwd=str(PROJECT), capture_output=False)
    ok = result.returncode == 0
    if desc:
        print("OK" if ok else f"FAILED (code={result.returncode})")
    return ok


def step1_install_deps() -> None:
    """安装打包所需的依赖"""
    print("[1/5] 安装依赖...")
    pip = [sys.executable, "-m", "pip", "install", "-q"]

    deps = [
        "flask", "openai", "pyyaml", "pywebview",
        "cryptography", "requests", "zstandard", "click",
        "pyinstaller",
        "pymem", "psutil",       # wechat-cli 核心依赖（进程内存读取）
    ]
    for dep in deps:
        result = subprocess.run(pip + [dep], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [WARN] {dep} 安装失败: {result.stderr[:200]}")
    # wechat-cli（GitHub 失败时 fallback 到 PyPI）
    r = subprocess.run(
        pip + ["wechat-cli @ git+https://github.com/fclwtt/wechat-cli.git"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        _ = subprocess.run(pip + ["wechat-cli"], capture_output=True)
    print("  [OK] 依赖安装完成\n")


def step2_clean() -> None:
    """清理旧构建"""
    print("[2/5] 清理旧构建...")
    for d in (PROJECT / "build", DIST_DIR):
        if d.exists():
            try:
                shutil.rmtree(d)
            except PermissionError:
                print(f"  [WARN] 无法删除 {d.name}，可能被占用")
    print("  [OK] 清理完成\n")


def step3_build() -> bool:
    """PyInstaller 打包"""
    # --- 自动递增版本号 ---
    new_ver = ver_mod.auto_bump()
    ver_file = ver_mod.generate_version_file(new_ver)
    ver_mod.update_run_py_version(new_ver)

    print("[3/5] 构建 EXE（可能需要 3-10 分钟）...")

    sep = ";" if sys.platform == "win32" else ":"

    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "WeChatWorkAgent",
        "--icon", str(PROJECT / "assets" / "app_icon.ico"),
        "--version-file", str(ver_file),
        "--onedir",
        "--windowed",
        "--additional-hooks-dir", str(PROJECT / "hooks"),
        "--add-data", f"config{sep}config",
        "--add-data", f"app{os.sep}static{sep}app{os.sep}static",
        "--add-data", f"wx_key{sep}wx_key",
        "--add-data", f"VERSION{sep}.",
        "--add-data", f"models{sep}models",
        "--paths", "src",
        "--paths", "app",
        # 在线授权模块（Flask/桌面入口都会引用，但 PyInstaller 可能分析不全）
        "--collect-all", "license_client",
        # src 模块（动态导入，需显式声明）
        "--collect-submodules", "src",
        # 第三方库
        "--hidden-import", "yaml",
        "--collect-all", "flask",
        "--collect-all", "webview",
        "--collect-all", "wechat_cli",
        "--collect-submodules", "wechat_cli",
        "--collect-all", "openai",
        "--collect-all", "pymem",
        "--collect-all", "psutil",
        # cryptography 含动态加载的 C 扩展，需全量收集
        "--collect-all", "cryptography",
        str(PROJECT / "run.py"),
    ]

    result = subprocess.run(cmd, cwd=str(PROJECT))
    if result.returncode != 0:
        print("\n  [FAILED] PyInstaller 构建失败!")
        return False
    print(f"  [OK] EXE 构建成功 (v{ver_mod.format_version(new_ver)})\n")
    return True


def step4_prepare() -> None:
    """准备输出目录和辅助文件"""
    print("[4/5] 准备分发目录...")
    out_dir = DIST_DIR / "output"
    logs_dir = DIST_DIR / "logs"
    out_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)

    # 安全处理：用脱敏模板覆盖真实 config.yaml
    example_config = PROJECT / "config" / "config.yaml.example"
    dist_config = DIST_DIR / "config" / "config.yaml"
    if example_config.exists() and dist_config.exists():
        print("  [\u5b89\u5168] 使用 config.yaml.example 替换 config.yaml（避免泄露凭据）")
        _ = shutil.copy(example_config, dist_config)

    # 将 wx_key 文件夹从 _internal 移动到顶层（避免双份占用空间）
    internal_wx_key = DIST_DIR / "_internal" / "wx_key"
    top_wx_key = DIST_DIR / "wx_key"
    if internal_wx_key.exists():
        if top_wx_key.exists():
            shutil.rmtree(top_wx_key)
        shutil.move(str(internal_wx_key), str(top_wx_key))
        print("  [wx_key] 已移到程序根目录（WeChatWorkAgent/wx_key/）")

    # 将 models 文件夹从 _internal 移动到顶层（避免双份占用空间——模型文件 ~491MB）
    internal_models = DIST_DIR / "_internal" / "models"
    top_models = DIST_DIR / "models"
    if internal_models.exists():
        if top_models.exists():
            shutil.rmtree(top_models)
        shutil.move(str(internal_models), str(top_models))
        print("  [models] 已移到程序根目录（WeChatWorkAgent/models/）")
    else:
        print("  [models] models 目录为空，未打包模型文件。运行时将自动下载。")

    print("  [OK] 目录已创建\n")

    print("[5/5] 生成辅助文件...")

    # 生成使用说明.txt
    _ = (DIST_DIR / "使用说明.txt").write_text(
        "微信工作日报助手 - 使用说明\n"
        "============================\n\n"
        "【第一步：准备 wx_key 工具】\n"
        "  将本目录下的 wx_key 文件夹，复制到非 C 盘的根目录下\n"
        "  （例如：D:\\wx_key\\ 或 E:\\wx_key\\）\n\n"
        "【第二步：安装依赖环境】\n"
        "  进入复制后的 wx_key 文件夹，双击运行 vc_redist.x64.exe\n"
        "  根据安装向导提示，完成 Visual C++ 运行库的安装\n\n"
        "【第三步：获取微信密钥】\n"
        "  在同目录下运行 wx_key.exe，根据程序提示操作\n"
        "  （程序会自动关闭正在运行的微信，并重启微信）\n"
        "  正常登录微信即可，随后工具会显示获取到的密钥\n\n"
        "【第四步：保存密钥】\n"
        "  复制 wx_key 工具显示的密钥（passphrase，一串十六进制字符串）\n"
        "  妥善保存，后续配置需要用到\n\n"
        "【第五步：启动软件】\n"
        "  回到本目录，双击运行 WeChatWorkAgent.exe\n"
        "  首次启动会弹出激活码输入框，请输入管理员提供的激活码完成激活\n"
        "  （格式: XXXX-XXXX-XXXX-XXXX）\n\n"
        "【第六步：配置密钥】\n"
        "  进入软件主界面后，点击左侧菜单「密钥配置」\n"
        "  在「手动密钥配置」区域，将第四步保存的 passphrase 粘贴到输入框\n"
        "  点击「配置密钥」按钮，等待提示「密钥配置成功」\n\n"
        "【第七步：软件设置】\n"
        "  按下方说明完成基本配置后即可正常使用\n\n\n"
        "----------------------------------------\n"
        "  软件功能说明\n"
        "----------------------------------------\n\n"
        "▶ 基本设置（左侧菜单第一个）\n"
        "  - 微信昵称（必填）：填写你自己的微信昵称，用于识别哪些消息是你发出的\n"
        "  - AI 配置（可选）：填写 API Key + Base URL + 模型名称，启用 AI 智能总结\n"
        "  - 输出格式：选择日报输出为 Markdown (.md) 或纯文本 (.txt)\n"
        "  - 屏蔽列表：添加不需要监控的联系人或群聊名称\n\n"
        "▶ 手动运行\n"
        "  立即分析今天的微信聊天记录，生成日报\n"
        "  输出文件保存在 output/手动运行_年月日_时分秒/ 目录下\n\n"
        "▶ 自动运行\n"
        "  无需手动操作，软件在设定时间自动分析聊天记录并生成日报\n"
        "  默认时间为每天 17:30（工作日下班前）\n\n"
        "▶ 定时任务\n"
        "  自定义自动运行的时间表和频率\n"
        "  支持设置每天、工作日、特定时间等\n\n"
        "▶ 规则逻辑\n"
        "  自定义 AI 分析的指令，用自然语言描述你希望 AI 关注的方面\n"
        "  例如：「重点关注项目进度、客户需求和风险点」\n\n"
        "▶ 会话分析\n"
        "  选择一个或多个群聊/联系人，进行全方位深度分析\n"
        "  分析内容：话题总结、决策记录、人员动态、待办事项、风险识别\n\n"
        "▶ 周报生成\n"
        "  根据一周的聊天记录，自动生成周报总结\n"
        "  可在定时任务中设置每周五自动生成\n\n\n"
        "----------------------------------------\n"
        "  常见问题\n"
        "----------------------------------------\n\n"
        "Q: 微信更新后无法读取消息？\n"
        "A: 用 wx_key 重新提取 passphrase，在基本设置中重新配置密钥即可\n\n"
        "Q: API 调用失败？\n"
        "A: 检查 API Key 和 Base URL 是否正确，网络是否通畅\n\n"
        "Q: 找不到聊天记录？\n"
        "A: 确认基本设置中「微信昵称」是否填写正确\n"
        "   确认微信桌面版已登录且有聊天记录\n\n"
        "Q: 激活失败？\n"
        "A: 确认激活码格式为 XXXX-XXXX-XXXX-XXXX，联系管理员获取有效激活码\n"
        "   如果更换了电脑，需要在新的电脑上重新激活\n",
        encoding="utf-8-sig",
    )

    print("  [OK] 辅助文件已生成\n")


def main() -> None:
    print("=" * 55)
    print("  微信工作日报助手 - PyInstaller 打包工具")
    print("=" * 55)
    print()

    step1_install_deps()
    step2_clean()

    if not step3_build():
        sys.exit(1)

    step4_prepare()

    # 同步版本号到 installer.iss
    new_ver = ver_mod.read_version()
    ver_mod.update_installer_iss(new_ver)

    # 计算输出大小
    total_size = sum(
        f.stat().st_size for f in DIST_DIR.rglob("*") if f.is_file()
    )
    size_mb = total_size / (1024 * 1024)

    exe_path = DIST_DIR / "WeChatWorkAgent.exe"
    exe_exists = exe_path.exists()
    ver_display = ver_mod.format_version(new_ver)

    print("=" * 55)
    if exe_exists:
        print(f"  [OK] 打包成功! ({ver_display})")
        print(f"  输出目录: {DIST_DIR}")
        print("  启动文件: WeChatWorkAgent.exe")
        print(f"  总大小:   {size_mb:.1f} MB")
        print()
        print("  分发方法:")
        print(f"  将 {DIST_DIR.name} 整个文件夹打包（zip/rar）发给对方")
        print("  对方解压后：")
        print("    1. 阅读使用说明.txt，按步骤操作")
        print("    2. 首次使用需要安装 vc_redist、提取微信密钥、激活授权")
        print()
        print("  [!] 对方无需安装 Python 或任何依赖!")
    else:
        print("  [FAILED] 打包失败，未找到 exe")
        print(f"     期望路径: {exe_path}")
    print("=" * 55)


if __name__ == "__main__":
    main()
