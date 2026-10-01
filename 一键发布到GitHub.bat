@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ============================================================
echo   盲道占用检测展示页 —— 发布到 GitHub Pages
echo ============================================================
echo.

cd /d "%~dp0"

rem ============================================================
rem  ★★★ 只需要改下面这一行：把地址换成你自己的仓库地址 ★★★
rem
rem  先去浏览器打开  https://github.com/new
rem  仓库名填      blindway-grand
rem  选           Public
rem  点           Create repository
rem  然后把地址粘到下面（注意把 snowcode-a 换成你的用户名）
rem ============================================================

set "REPO=https://github.com/snowcode-a/blindway-grand.git"

rem ============================================================
rem  下面不用改
rem ============================================================

echo 目标仓库: %REPO%
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo [错误] 没找到 git，请先安装: https://git-scm.com/download/win
    echo.
    pause
    exit /b 1
)

if not exist ".git" (
    echo [1/5] 初始化本地仓库 ...
    git init -b main
) else (
    echo [1/5] 本地仓库已存在
)

echo [2/5] 暂存文件 ...
git add -A

echo [3/5] 提交 ...
git -c user.name="snowcode-a" -c user.email="3298672061@qq.com" ^
    commit -m "盲道占用检测系统 Blindway Grand - 界面演示与项目说明" >nul 2>nul
if errorlevel 1 (echo      没有新改动) else (echo      已提交)

echo [4/5] 关联远程仓库 ...
git remote remove origin >nul 2>nul
git remote add origin "%REPO%"
git branch -M main

echo [5/5] 推送到 GitHub ...
echo.
echo   >>> 如果弹出浏览器让你登录 GitHub，登录一下即可 <<<
echo.
git push -u origin main

if errorlevel 1 (
    echo.
    echo ============================================================
    echo   推送失败。按下面排查：
    echo.
    echo   1^) 仓库地址写错  -- 检查脚本里 REPO= 那行
    echo   2^) 没登录        -- 重新运行本脚本，按提示登录
    echo   3^) 提示 403      -- 去 https://github.com/settings/tokens
    echo                        生成 classic token（勾 repo），
    echo                        推送时用户名填 GitHub 用户名，
    echo                        密码处粘贴 token
    echo ============================================================
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   推送成功！
echo.
echo   最后一步（不做的话链接打不开，务必做）：
echo.
echo     1. 打开你的仓库页面
echo     2. 点右上角  Settings
echo     3. 左侧菜单找到  Pages
echo     4. Source 选       Deploy from a branch
echo        Branch 选       main
echo        右边目录选       / (root)
echo     5. 点  Save，等 1~2 分钟
echo.
echo   然后这个链接就能给评委了（手机也能开）：
echo.
echo     https://你的GitHub用户名.github.io/blindway-grand/
echo.
echo ============================================================
echo.
pause
