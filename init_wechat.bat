@echo off
title 微信工作日报助手 - 初始化

cd /d "%~dp0"

echo ============================================
echo   微信工作日报助手 - 初始化 wechat-cli
echo ============================================
echo.
echo 请确保微信桌面版正在运行并已登录！
echo.

call venv\Scripts\activate.bat
python -m wechat_cli init

echo.
echo 初始化完成！现在可以运行:
echo   run_gui.bat - 启动桌面应用
echo   python main.py - 命令行模式
echo.

pause
