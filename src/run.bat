@echo off
chcp 65001 >nul
title 盲道占用检测 · Blindway Grand
cd /d "%~dp0"

echo ============================================================
echo   盲道占用检测系统 · Blindway Grand v2.0
echo ============================================================
echo.

rem ---- 自动找 Python，不写死路径，别人克隆后也能直接跑 ----
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py"
)
if not defined PY (
    echo [错误] 没找到 Python。请先安装 Python 3.10 以上版本：
    echo        https://www.python.org/downloads/
    echo        安装时记得勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo 使用解释器: %PY%
echo.

rem ---- 检查依赖，缺了自动装 ----
"%PY%" -c "import torch, cv2, PyQt5, ultralytics" 2>nul
if errorlevel 1 (
    echo [提示] 依赖不完整，正在安装（首次约需几分钟，请耐心等待）...
    echo.
    "%PY%" -m pip install torch torchvision opencv-python PyQt5 ultralytics numpy Pillow hyperlpr3 onnxruntime
    if errorlevel 1 (
        echo.
        echo [错误] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
    echo.
    echo [完成] 依赖已安装
    echo.
)

if not exist "yolov8n.pt" (
    echo [提示] 缺少 yolov8n.pt，首次运行会自动下载（约 6MB）
    echo.
)

echo 正在启动界面 ...
echo.
"%PY%" qt_app.py

if errorlevel 1 (
    echo.
    echo [程序异常退出] 错误码 %errorlevel%
    echo 常见原因：
    echo   1. 依赖没装全   -- 重新运行本脚本，它会自动补装
    echo   2. 视频文件缺失 -- 到「实时监控」页点「选择视频…」挑一个
    echo.
    pause
)
