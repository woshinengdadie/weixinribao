@echo off
chcp 65001 >nul
title 微信工作日报助手 - 一键打包（EXE + 安装包）

cd /d "%~dp0"

echo ============================================
echo   微信工作日报助手 - 一键打包
echo ============================================
echo.

call build_exe.bat
if %errorlevel% neq 0 (
    echo.
    echo [错误] EXE 打包失败，停止。
    pause
    exit /b 1
)

echo.
call build_installer.bat
if %errorlevel% neq 0 (
    echo.
    echo [错误] 安装包打包失败。
    pause
    exit /b 1
)

echo ============================================
echo   全部完成！安装包在 installer_output\
echo ============================================
echo.

pause
