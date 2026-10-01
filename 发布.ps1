# ============================================================================
#  盲道占用检测展示页 —— 发布到 GitHub Pages
#  用法：把下面整段复制到 PowerShell 里回车
# ============================================================================

# ★★★ 只改这一行：换成你的 GitHub 用户名 ★★★
$GH_USER = "snowcode-a"
$REPO_NAME = "blindway-grand"

# ---------------------------------------------------------------------------
#  以下不用改
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"
$repoUrl = "https://github.com/$GH_USER/$REPO_NAME.git"

# 定位到本脚本所在目录（也就是展示页仓库根目录）
$here = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location $here

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  发布到 GitHub Pages" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  本地目录 : $here"
Write-Host "  目标仓库 : $repoUrl"
Write-Host ""

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "[错误] 没找到 git，请先安装 https://git-scm.com/download/win" -ForegroundColor Red
    return
}

# 1) 初始化 / 提交
if (-not (Test-Path ".git")) { git init -b main | Out-Null; Write-Host "[1/4] 初始化仓库" }
else { Write-Host "[1/4] 仓库已存在" }

git add -A
git -c user.name="$GH_USER" -c user.email="$GH_USER@users.noreply.github.com" `
    commit -m "盲道占用检测系统 Blindway Grand - 界面演示与项目说明" 2>$null | Out-Null
Write-Host "[2/4] 已提交"

# 2) 关联远程
git remote remove origin 2>$null | Out-Null
git remote add origin $repoUrl
git branch -M main
Write-Host "[3/4] 已关联 $repoUrl"

# 3) 推送
Write-Host "[4/4] 正在推送（首次会弹出浏览器让你登录 GitHub，登录后即可）..." -ForegroundColor Yellow
Write-Host ""
git push -u origin main

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "推送失败，常见原因：" -ForegroundColor Red
    Write-Host "  1) 仓库还没建 —— 先去 https://github.com/new 建一个叫 $REPO_NAME 的 Public 仓库"
    Write-Host "  2) 用户名写错 —— 检查脚本顶部的 GH_USER"
    Write-Host "  3) 提示 403   —— 去 https://github.com/settings/tokens 生成 classic token（勾 repo），"
    Write-Host "                   推送时用户名填 GitHub 用户名，密码处粘贴 token"
    return
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  推送成功！" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  ★ 最后一步（不做的话链接打不开）：" -ForegroundColor Yellow
Write-Host "    1. 打开 https://github.com/$GH_USER/$REPO_NAME/settings/pages"
Write-Host "    2. Source 选  Deploy from a branch"
Write-Host "    3. Branch 选  main   目录选  / (root)   点 Save"
Write-Host "    4. 等 1~2 分钟"
Write-Host ""
Write-Host "  然后这个链接就能发给评委了（手机也能打开）：" -ForegroundColor Cyan
Write-Host ""
Write-Host "    https://$GH_USER.github.io/$REPO_NAME/" -ForegroundColor Cyan
Write-Host ""
