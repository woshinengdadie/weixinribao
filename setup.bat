@echo off
title 微信工作日报助手 - 一键安装

cd /d "%~dp0"

echo ============================================
echo   微信工作日报助手 - 一键安装脚本
echo ============================================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，正在下载安装...
    echo.
    echo 请手动安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    echo 安装时请勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
echo [OK] Python 已安装:
python --version
echo.

:: 创建虚拟环境
if not exist "venv" (
    echo [1/5] 创建虚拟环境...
    python -m venv venv
    echo [OK] 虚拟环境已创建
) else (
    echo [1/5] 虚拟环境已存在，跳过
)
echo.

:: 更新 pip
echo [2/5] 更新 pip...
call venv\Scripts\python.exe -m pip install --upgrade pip -q
echo.

:: 安装依赖
echo [3/5] 安装依赖包...
call venv\Scripts\pip.exe install pywebview flask openai pyyaml cryptography requests -q
if %errorlevel% neq 0 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)
echo   - Flask + PyWebView... OK

call venv\Scripts\pip.exe install zstandard click pymem psutil -q
echo   - 工具依赖... OK

:: 安装 wechat-cli（核心依赖：微信消息读取）
echo   - 安装 wechat-cli（微信消息读取）...
call venv\Scripts\pip.exe install "wechat-cli @ git+https://github.com/fclwtt/wechat-cli.git" -q
if %errorlevel% neq 0 (
    echo [警告] wechat-cli 从 GitHub 安装失败，尝试从 PyPI...
    call venv\Scripts\pip.exe install wechat-cli -q
)
echo   - wechat-cli... OK
echo.

:: 创建目录
echo [4/5] 创建输出目录...
if not exist "output" mkdir output
if not exist "logs" mkdir logs
echo [OK] 目录已创建
echo.

:: 初始化 wechat-cli
echo [5/5] 初始化 wechat-cli（提取微信密钥）...
echo.
echo ============================================
echo   请确保微信桌面版正在运行并已登录！
echo ============================================
echo.
set /p INIT_CHOICE="是否现在初始化？(Y/N，默认 Y): "
if "%INIT_CHOICE%"=="" set INIT_CHOICE=Y
if /i "%INIT_CHOICE%"=="Y" (
    call venv\Scripts\python.exe -m wechat_cli init
    if %errorlevel% neq 0 (
        echo [警告] wechat-cli 初始化失败
        echo   请稍后手动运行: venv\Scripts\python.exe -m wechat_cli init
    )
) else (
    echo   已跳过，请稍后运行: venv\Scripts\python.exe -m wechat_cli init
)
echo.

:: 完成
echo ============================================
echo   安装完成！
echo.
echo   运行方式:
echo   1. GUI 桌面版: 双击 run_gui.bat
echo   2. 命令行版: venv\Scripts\python.exe main.py
echo   3. 初始化密钥: venv\Scripts\python.exe -m wechat_cli init
echo.
echo   首次使用请先配置:
echo   - 编辑 config\app_settings.json 设置微信昵称
echo   - 编辑 config\config.yaml 设置 LLM API Key
echo ============================================
echo.

pause
