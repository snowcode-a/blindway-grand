@echo off
chcp 65001 >nul
setlocal

echo ============================================================
echo   盲道占用检测 · 展示页 —— 一键发布到 GitHub Pages
echo ============================================================
echo.

cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo [错误] 没找到 git。请先安装 Git: https://git-scm.com/download/win
    pause
    exit /b 1
)

echo 当前目录: %CD%
echo.

rem ---- 第 1 步：初始化本地仓库 ----
if not exist ".git" (
    echo [1/5] 初始化本地仓库 ...
    git init -b main
) else (
    echo [1/5] 本地仓库已存在，跳过初始化
)

echo [2/5] 暂存文件 ...
git add -A

echo [3/5] 提交 ...
git commit -m "盲道占用检测系统 Blindway Grand - 界面演示与项目说明" 2>nul
if errorlevel 1 (
    echo      没有新改动需要提交，继续
)

echo.
echo ------------------------------------------------------------
echo  接下来需要填你的仓库地址。
echo.
echo  请先在浏览器里做两件事：
echo    1. 打开 https://github.com/new
echo    2. 仓库名填  blindway-grand  ，选 Public ，点 Create
echo.
echo  建好后把仓库地址复制过来，格式类似：
echo    https://github.com/snowcode-a/blindway-grand.git
echo ------------------------------------------------------------
echo.
set /p REPO=请粘贴仓库地址后回车:

if "%REPO%"=="" (
    echo [取消] 没填地址，已停止。
    pause
    exit /b 1
)

echo.
echo [4/5] 关联远程仓库 ...
git remote remove origin 2>nul
git remote add origin "%REPO%"

echo [5/5] 推送到 GitHub ...
git push -u origin main
if errorlevel 1 (
    echo.
    echo [失败] 推送没成功。常见原因：
    echo   1. 仓库地址填错了
    echo   2. 需要登录：浏览器会弹出登录窗口，登录后再运行一次这个脚本
    echo   3. 如果提示 403，去 GitHub 设置里生成一个 Personal Access Token，
    echo      推送时用户名填 GitHub 用户名，密码位置粘贴 token
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   推送成功！
echo.
echo   最后一步（很重要，做了评委才能用链接打开）：
echo     1. 打开你的仓库页面
echo     2. 点 Settings  ->  左侧 Pages
echo     3. Source 选  Deploy from a branch
echo        Branch 选  main  ，目录选  / (root)  ，点 Save
echo     4. 等 1~2 分钟，链接就是：
echo.
echo        https://你的用户名.github.io/blindway-grand/
echo.
echo   把这个链接发给评委即可，手机也能打开。
echo ============================================================
echo.
pause
