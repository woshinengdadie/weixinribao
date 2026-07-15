@echo off
chcp 65001 >nul
title 微信工作日报助手

cd /d "%~dp0"

echo 正在启动桌面版...
echo.

call venv\Scripts\activate.bat
python run.py

if %errorlevel% neq 0 (
    echo.
    echo [错误] 启动失败
    echo 请先运行 setup.bat 安装依赖环境
    pause
)
