@echo off
cd /d "%~dp0"
title 微信工作日报助手 - 安装定时任务

echo ========================================
echo    ? 安装 Windows 定时任务
echo ========================================
echo.
echo 此操作将创建定时任务，每天定时生成工作日报。
echo 请以管理员身份运行此脚本。
echo.

:: 获取当前目录（绝对路径）
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "VENV_PYTHON=%SCRIPT_DIR%\venv\Scripts\python.exe"
set "MAIN_SCRIPT=%SCRIPT_DIR%\main.py"

:: 检查虚拟环境
if not exist "%VENV_PYTHON%" (
    echo ? 未找到虚拟环境，请先运行 setup.bat
    pause
    exit /b 1
)

echo 脚本路径: %MAIN_SCRIPT%
echo Python路径: %VENV_PYTHON%
echo.

:: 询问运行时间
set /p RUN_HOUR=请输入每日运行时间（小时，0-23，默认 18）: 
if "%RUN_HOUR%"=="" set RUN_HOUR=18

set /p RUN_MINUTE=请输入每日运行时间（分钟，0-59，默认 0）: 
if "%RUN_MINUTE%"=="" set RUN_MINUTE=0

echo.
echo ? 正在创建定时任务 "WeChatWorkDailyReport"...
echo    运行时间: 每天 %RUN_HOUR%:%RUN_MINUTE%
echo.

:: 创建定时任务
schtasks /create ^
    /tn "WeChatWorkDailyReport" ^
    /tr "\"%VENV_PYTHON%\" \"%MAIN_SCRIPT%\"" ^
    /sc daily ^
    /st %RUN_HOUR%:%RUN_MINUTE% ^
    /f

if %errorlevel% equ 0 (
    echo ? 定时任务创建成功！
    echo    每天 %RUN_HOUR%:%RUN_MINUTE% 自动生成日报
    echo    任务名称: WeChatWorkDailyReport
) else (
    echo ? 定时任务创建失败！
    echo    请尝试以管理员身份运行此脚本
)
echo.

:: 询问是否也创建开机自启任务
echo ? 是否也创建一个开机后持续监听的任务？（每隔30分钟检查一次）
set /p ADD_WATCH=是否创建？（y/n，默认 n）: 
if /i "%ADD_WATCH%"=="y" (
    schtasks /create ^
        /tn "WeChatWorkWatch" ^
        /tr "\"%VENV_PYTHON%\" \"%MAIN_SCRIPT%\" --watch --interval 30" ^
        /sc onlogon ^
        /delay 0005:00 ^
        /f

    if %errorlevel% equ 0 (
        echo ? 监听任务创建成功！
        echo    登录后自动启动，每30分钟检查一次
    ) else (
        echo ? 监听任务创建失败
    )
)

echo.
echo 管理定时任务: 在搜索框输入"任务计划程序"即可查看和修改
echo.
pause
