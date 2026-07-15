@echo off
chcp 65001 >nul
title 硬件码采集

cd /d "%~dp0"

:: 查找 Python
set "PYTHON="
python --version >nul 2>&1 && set "PYTHON=python"
if "%PYTHON%"=="" if exist "venv\Scripts\python.exe" set "PYTHON=venv\Scripts\python.exe"
if "%PYTHON%"=="" (
    echo [错误] 未找到 Python，请先运行 setup.bat 或安装 Python 3
    pause
    exit /b 1
)

echo ============================================
echo   微信工作日报助手 - 本机硬件码
echo ============================================
echo.

:: 调用与主程序完全一致的采集模块
%PYTHON% -m license_client.hardware_id

echo.
echo ============================================
echo   将此硬件码发给管理员用于生成激活码
echo ============================================
pause
