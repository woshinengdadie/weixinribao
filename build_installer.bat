@echo off
chcp 936 >nul
title 微信工作日报助手 - 生成安装包

cd /d "%~dp0"

echo ============================================
echo   微信工作日报助手 - 生成安装包
echo ============================================
echo.

:: 检查 Inno Setup（多路径查找）
set "ISCC="
if exist "C:\InnoSetup6\ISCC.exe" set "ISCC=C:\InnoSetup6\ISCC.exe"
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files (x86)\Inno Setup 5\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 5\ISCC.exe"

if "%ISCC%"=="" (
    echo [错误] 未找到 Inno Setup！
    echo.
    echo 请下载安装 Inno Setup:
    echo   https://jrsoftware.org/isdl.php
    echo.
    echo 安装后重新运行本脚本即可。
    pause
    exit /b 1
)
echo [OK] Inno Setup 编译器: %ISCC%
echo.

:: 检查是否已打包
if not exist "dist\WeChatWorkAgent\WeChatWorkAgent.exe" (
    echo [错误] 未找到打包产物！
    echo 请先运行 build_exe.bat 完成打包。
    pause
    exit /b 1
)
echo [OK] 打包产物已就绪
echo.

:: 清理旧的安装包
if exist "installer_output" (
    rmdir /s /q "installer_output" 2>nul
)

:: 编译安装包
echo [1/2] 编译安装包...
"%ISCC%" /Q "installer.iss"
if %errorlevel% neq 0 (
    echo.
    echo [错误] 安装包编译失败！请检查 installer.iss 是否正确。
    pause
    exit /b 1
)

:: 输出版本信息
echo [OK] 安装包编译成功！
echo.

echo ============================================
echo   OK 安装包生成完成！
echo.
echo   文件: installer_output\WeChatWorkAgent_Setup_*.exe
echo.
echo   将此文件发给对方即可。
echo   对方安装后：
echo     1. 确保微信已登录
echo     2. 开始菜单 - 微信工作日报助手 - 初始化密钥
echo     3. 双击桌面快捷方式启动
echo ============================================
echo.

pause
