@echo off
chcp 65001 >nul
title 微信工作日报助手 - 打包 EXE

cd /d "%~dp0"

echo ============================================
echo   微信工作日报助手 - 构建独立 EXE（可分发）
echo ============================================
echo.

::: 检查 Python（多路径查找）
set "PYTHON="

::: 1. 优先用系统 PATH 中的 python
python --version >nul 2>&1
if %errorlevel% equ 0 set "PYTHON=python"

::: 2. 尝试 Windows Python Launcher
if "%PYTHON%"=="" (
    py -3 --version >nul 2>&1
    if %errorlevel% equ 0 set "PYTHON=py -3"
)

::: 3. 尝试本地 venv
if "%PYTHON%"=="" (
    if exist "venv\Scripts\python.exe" (
        venv\Scripts\python.exe --version >nul 2>&1
        if %errorlevel% equ 0 set "PYTHON=venv\Scripts\python.exe"
    )
)

::: 4. 尝试常见安装路径
if "%PYTHON%"=="" (
    for %%p in (
        "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python314\python.exe"
        "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python313\python.exe"
        "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python312\python.exe"
        "C:\Program Files\Python314\python.exe"
        "C:\Program Files\Python313\python.exe"
        "C:\Python314\python.exe"
    ) do (
        if exist %%p (
            set "PYTHON=%%~p"
            goto :found_python
        )
    )
)

:found_python
if "%PYTHON%"=="" (
    echo [错误] 未找到 Python 3
    echo 请安装 Python 3.10+ 并勾选 "Add Python to PATH"
    echo 下载: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python 已就绪: %PYTHON%
%PYTHON% --version
echo.

::: 创建/激活虚拟环境
if not exist "venv" (
    echo [1/6] 创建虚拟环境...
    %PYTHON% -m venv venv
    if %errorlevel% neq 0 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
)
echo [1/6] 激活虚拟环境...
call venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo [错误] 激活虚拟环境失败
    pause
    exit /b 1
)

::: 激活后重新设置 PYTHON 为 venv 中的 python
set "PYTHON=venv\Scripts\python.exe"

::: 安装依赖（使用 python -m pip，避免 pip 命令未加入 PATH 的问题）
echo [2/6] 安装项目依赖...
%PYTHON% -m pip install -U pip -q 2>nul

::: 核心依赖
%PYTHON% -m pip install flask openai pyyaml pywebview cryptography requests -q
if %errorlevel% neq 0 (
    echo [错误] 核心包安装失败
    pause
    exit /b 1
)
echo   [OK] Flask + OpenAI + PyYAML + PyWebView + cryptography + requests

::: 加密和工具依赖
%PYTHON% -m pip install zstandard click pymem psutil -q
echo   [OK] zstandard + click + pymem + psutil

::: wechat-cli（微信消息读取核心）
%PYTHON% -m pip install "wechat-cli @ git+https://github.com/fclwtt/wechat-cli.git" -q 2>nul
if %errorlevel% neq 0 (
    %PYTHON% -m pip install wechat-cli -q
)
echo   [OK] wechat-cli

::: PyInstaller 打包工具
%PYTHON% -m pip install pyinstaller -q
if %errorlevel% neq 0 (
    echo [错误] PyInstaller 安装失败
    pause
    exit /b 1
)
echo   [OK] PyInstaller
echo.

::: 清理旧构建
echo [3/6] 清理旧构建...
if exist "dist\WeChatWorkAgent" (
    rmdir /s /q "dist\WeChatWorkAgent" 2>nul
)
if exist "build" (
    rmdir /s /q "build" 2>nul
)
echo [OK] 清理完成
echo.

::: 自动递增版本号 + 生成版本信息文件
echo [3.5/6] 更新版本号...
%PYTHON% tools\version.py bump
if %errorlevel% neq 0 (
    echo   [警告] 版本号递增失败，使用当前版本
)
%PYTHON% tools\version.py gen-file -o build\version_info.txt
%PYTHON% tools\version.py update-run
%PYTHON% tools\version.py update-changelog
echo   [OK] 版本号已更新
echo.

::: PyInstaller 构建
echo [4/6] 开始构建 EXE（可能需要 3-10 分钟）...
echo   正在打包，请耐心等待...
echo.

%PYTHON% -m PyInstaller --noconfirm --clean ^
    --name "WeChatWorkAgent" ^
    --icon "assets\app_icon.ico" ^
    --version-file "build\version_info.txt" ^
    --onedir ^
    --windowed ^
    --additional-hooks-dir "hooks" ^
    --add-data "config;config" ^
    --add-data "app\static;app\static" ^
    --add-data "wx_key;wx_key" ^
    --add-data "VERSION;." ^
    --add-data "models;models" ^
    --paths "src" ^
    --paths "app" ^
    --collect-submodules "src" ^
    --collect-all "license_client" ^
    --hidden-import "yaml" ^
    --collect-all "flask" ^
    --collect-all "webview" ^
    --collect-all "wechat_cli" ^
    --collect-submodules "wechat_cli" ^
    --collect-all "openai" ^
    --collect-all "pymem" ^
    --collect-all "psutil" ^
    --collect-all "cryptography" ^
    run.py

if %errorlevel% neq 0 (
    echo.
    echo [错误] PyInstaller 构建失败！
    echo 请检查上面的错误信息。
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] EXE 构建成功！
echo.

::: 同步版本号到 installer.iss
%PYTHON% tools\version.py update-iss
echo   [OK] installer.iss 版本号已同步
echo.

::: 创建输出目录模板
echo [5/6] 准备输出目录...
if not exist "dist\WeChatWorkAgent\output" mkdir "dist\WeChatWorkAgent\output"
if not exist "dist\WeChatWorkAgent\logs"    mkdir "dist\WeChatWorkAgent\logs"

::: 安全处理：用脱敏模板覆盖真实 config.yaml，避免泄露 API Key 等敏感信息
if exist "config\config.yaml.example" (
    echo   [安全] 使用 config.yaml.example 替换 config.yaml（避免泄露凭据）
    copy /Y "config\config.yaml.example" "dist\WeChatWorkAgent\config\config.yaml" >nul
)
echo [OK] 准备完成
echo.

::: 将 wx_key 从 _internal 移到顶层（与 exe 同级，避免双份）
if exist "dist\WeChatWorkAgent\_internal\wx_key" (
    echo   [wx_key] 移到程序根目录
    if exist "dist\WeChatWorkAgent\wx_key" rmdir /s /q "dist\WeChatWorkAgent\wx_key"
    move "dist\WeChatWorkAgent\_internal\wx_key" "dist\WeChatWorkAgent\wx_key" >nul 2>&1
)
echo [OK] wx_key 已就位

:::: 将 models 文件夹从 _internal 移动到顶层（删掉 _internal 内的副本避免双份）
if exist "dist\WeChatWorkAgent\_internal\models" (
    echo   [models] 移到程序根目录（避免双份占用空间）
    if exist "dist\WeChatWorkAgent\models" rmdir /s /q "dist\WeChatWorkAgent\models"
    move "dist\WeChatWorkAgent\_internal\models" "dist\WeChatWorkAgent\models" >nul 2>&1
)
if exist "dist\WeChatWorkAgent\models" (
    echo [OK] 本地模型已就位
) else (
    echo   [模型] models 目录为空，未打包模型文件。运行时将自动下载。
)


::: 生成分发辅助文件
echo [6/6] 生成分发辅助文件...
powershell -NoProfile -ExecutionPolicy Bypass -File "_gen_dist_files.ps1" -DistDir "dist\WeChatWorkAgent"
echo.

::: 计算文件夹大小
for /f "tokens=3" %%a in ('dir /s /-c "dist\WeChatWorkAgent" 2^>nul ^| findstr "个文件"') do set TOTAL_FILES=%%a
for /f "tokens=3" %%a in ('dir /s /-c "dist\WeChatWorkAgent" 2^>nul ^| findstr "字节"') do set TOTAL_SIZE=%%a

echo ============================================
echo.   🚀 下一步：发布更新
echo.   请打开以下页面，把 installer_output\ 下的安装包拖进去发布：
echo.   https://github.com/woshinengdadie/weixinribao/releases/new
echo ============================================
echo.

start https://github.com/woshinengdadie/weixinribao/releases/new
pause
