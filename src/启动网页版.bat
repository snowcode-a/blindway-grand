@echo off
chcp 65001 >nul
title 盲道占用检测 · 网页镜像服务
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py"
)
if not defined PY (
    echo [错误] 没找到 Python，请先安装 Python 3.10 以上版本
    pause
    exit /b 1
)

echo ============================================================
echo   盲道占用检测 · 网页镜像
echo ============================================================
echo.
echo   启动后会打印可访问的网址，用浏览器打开即可。
echo   手机在同一 WiFi 下也能打开（扫网页上的二维码）。
echo.
echo   关闭这个黑窗口 = 服务停止。
echo ============================================================
echo.

"%PY%" web_mirror.py --port 8080

if errorlevel 1 (
    echo.
    echo [出错] 服务异常退出，把上面的红字截图发给开发者。
    pause
)
