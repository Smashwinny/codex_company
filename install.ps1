# codex-quota Windows 一键安装：
#   1. 检查依赖（Python ≥ 3.10 / codex CLI / kimi CLI 可选）
#   2. 创建 .venv 并安装 PyQt6
#   3. 下载 cloudflared（vendor/bin/cloudflared.exe，手机公网访问隧道，可选）
#   4. 创建开始菜单快捷方式（Codex Quota.lnk）
# 幂等：重复运行安全。
#
# 执行策略拦脚本时这样跑：
#   powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Windows PowerShell 下载进度渲染很慢
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

function ok($msg)   { Write-Host "    OK: $msg" }
function warn($msg) { Write-Host "    ⚠ $msg" -ForegroundColor Yellow }

Write-Host "==> [1/4] 检查依赖"

# --- Python ≥ 3.10（py launcher 优先，回退 python） ---
$pyExe = $null; $pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pyExe = "py"; $pyArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pyExe = "python"
}
if (-not $pyExe) {
    Write-Host "错误：未找到 Python。请先安装 Python ≥ 3.10（python.org，勾选 Add to PATH）" -ForegroundColor Red
    exit 1
}
& $pyExe @pyArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "错误：Python 过旧或不可用，需要 ≥ 3.10" -ForegroundColor Red
    exit 1
}
$pyVer = & $pyExe @pyArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
ok "python $pyVer（$pyExe）"

# --- codex CLI（数据源，必需） ---
if (Get-Command codex -ErrorAction SilentlyContinue) {
    ok "codex CLI: $((Get-Command codex).Source)"
} else {
    warn "未找到 codex CLI —— Codex 额度将无法获取。"
    warn "  安装: npm i -g @openai/codex 然后 codex login"
}

# --- kimi CLI（可选 provider） ---
if ((Get-Command kimi -ErrorAction SilentlyContinue) -or
    (Test-Path "$HOME\.kimi-code\bin\kimi.exe") -or
    (Test-Path "$HOME\.kimi-code\bin\kimi.cmd")) {
    ok "kimi CLI 已检测到（Kimi 分区将自动启用）"
} else {
    warn "未找到 kimi CLI —— 仅显示 Codex（安装后自动启用，无需重装）"
}

Write-Host "==> [2/4] Python 虚拟环境（PyQt6）"
$venvPy = "$ROOT\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & $pyExe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "错误：venv 创建失败" -ForegroundColor Red; exit 1 }
}
# 真 import 探测（find_spec 只查文件存在，DLL 损坏/缺 VC++ 运行库会漏判）
& $venvPy -c "import PyQt6" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $venvPy -m pip install -q PyQt6
    if ($LASTEXITCODE -ne 0) { Write-Host "错误：PyQt6 安装失败" -ForegroundColor Red; exit 1 }
}
$qtVer = & $venvPy -c "import PyQt6.QtCore as c; print(c.QT_VERSION_STR)"
if ($LASTEXITCODE -ne 0 -or -not $qtVer) {
    Write-Host "错误：PyQt6 已安装但无法导入——通常缺 Visual C++ 运行库（安装微软官方 vc_redist.x64.exe 后重跑）" -ForegroundColor Red
    exit 1
}
ok "PyQt6 $qtVer"

# 把项目装进 venv，确保注册表 Run 从任意工作目录执行
# `pythonw.exe -m codex_quota` 都能找到模块（editable 便于原地更新代码）。
& $venvPy -m pip install -q -e $ROOT
if ($LASTEXITCODE -ne 0) {
    Write-Host "错误：codex-quota 安装进 venv 失败" -ForegroundColor Red
    exit 1
}
$pkgPath = & $venvPy -I -c "import codex_quota; print(codex_quota.__file__)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "错误：codex-quota 跨工作目录导入验证失败" -ForegroundColor Red
    exit 1
}
ok "codex-quota editable install（$pkgPath）"

Write-Host "==> [3/4] cloudflared（手机公网访问隧道，可选但推荐）"
$cfBin = "$ROOT\vendor\bin\cloudflared.exe"
if (Get-Command cloudflared -ErrorAction SilentlyContinue) {
    ok "系统已安装: $((Get-Command cloudflared).Source)"
} elseif (Test-Path $cfBin) {
    ok "vendor/bin/ 已存在"
} else {
    Write-Host "    下载 cloudflared（用于手机 4G/外出访问）..."
    New-Item -ItemType Directory -Force -Path "$ROOT\vendor\bin" | Out-Null
    try {
        Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
                          -OutFile $cfBin -UseBasicParsing
        ok "vendor/bin/cloudflared.exe"
    } catch {
        warn "下载失败（仅局域网访问，不影响其他功能）：$_"
        warn "  可手动下载 cloudflared-windows-amd64.exe 放到 vendor\bin\ 后重跑"
    }
}

Write-Host "==> [4/4] 开始菜单快捷方式"
$lnkPath = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\Codex Quota.lnk"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = "$ROOT\.venv\Scripts\pythonw.exe"   # 直指 pythonw，不弹控制台黑窗
$lnk.Arguments = "-m codex_quota"
$lnk.WorkingDirectory = $ROOT
$icon = "$ROOT\assets\codex-quota.ico"
if (Test-Path $icon) { $lnk.IconLocation = $icon }
$lnk.Save()
ok $lnkPath

Write-Host ""
Write-Host "安装完成！启动方式（任选）："
Write-Host "  · 开始菜单搜索 ""Codex Quota"" 点击启动"
Write-Host "  · 命令行: bin\codex-quota.cmd"
Write-Host "  · 开机自启: 启动后在托盘菜单勾选 ""开机自启"""
Write-Host "  · 已在运行时重复启动只会提示/激活已有窗口，不会开第二个实例"
Write-Host ""
Write-Host "日志: `$env:LOCALAPPDATA`\codex-quota\hud.log"
Write-Host ""

try {
    $ans = Read-Host "现在启动 Codex Quota？[Y/n]"
    if ($ans -notin @("n", "N")) {
        Start-Process "$ROOT\.venv\Scripts\pythonw.exe" -ArgumentList "-m", "codex_quota" -WorkingDirectory $ROOT
        Write-Host "已启动。稍后想启动：开始菜单搜 Codex Quota，或 bin\codex-quota.cmd"
    } else {
        Write-Host "稍后想启动时：开始菜单搜 Codex Quota，或 bin\codex-quota.cmd"
    }
} catch {
    # 非交互环境（无控制台）跳过询问
}
