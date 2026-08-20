#!/usr/bin/env bash
# codex-quota 一键安装：
#   1. 检查依赖（python3 ≥ 3.10 / codex CLI / kimi CLI 可选）
#   2. 准备 .venv 并安装 PyQt6
#   3. 确保 libxcb-cursor 可用（无 root 时下载 .deb 解压到 vendor/）
#   4. 确保 cloudflared 可用（手机公网访问隧道，可选）
#   5. 创建应用菜单桌面入口（~/.local/share/applications/codex-quota.desktop）
#   6. 软链 codex-quota 命令到 ~/.local/bin（终端直接敲名字启动）
# 幂等：重复运行安全。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

warn() { echo "    ⚠ $*"; }
ok()   { echo "    OK: $*"; }

echo "==> [1/6] 检查依赖"

# --- python3 ≥ 3.10 ---
if ! command -v python3 >/dev/null; then
    echo "错误：未找到 python3。请先安装 Python ≥ 3.10（如 sudo apt install python3）" >&2
    exit 1
fi
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "错误：Python $PYVER 过旧，需要 ≥ 3.10" >&2
    exit 1
}
ok "python3 $PYVER"

# --- codex CLI（数据源，必需） ---
if command -v codex >/dev/null; then
    ok "codex CLI: $(command -v codex)"
else
    warn "未找到 codex CLI —— Codex 额度将无法获取。"
    warn "  安装: npm i -g @openai/codex && codex login"
fi

# --- kimi CLI（可选 provider） ---
if command -v kimi >/dev/null || [ -x "$HOME/.kimi-code/bin/kimi" ]; then
    ok "kimi CLI 已检测到（Kimi 分区将自动启用）"
else
    warn "未找到 kimi CLI —— 仅显示 Codex（安装后自动启用，无需重装）"
fi

echo "==> [2/6] Python 虚拟环境（PyQt6）"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
    if command -v virtualenv >/dev/null; then
        virtualenv .venv
    elif python3 -m venv .venv 2>/dev/null; then
        :
    else
        # python3-venv 缺失（Ubuntu 常见）：用 pip --user 装 virtualenv 兜底
        rm -rf .venv
        echo "    python3-venv 缺失，安装 virtualenv 到用户目录 ..."
        python3 -m pip install --user virtualenv
        python3 -m virtualenv .venv
    fi
fi
if ! "$ROOT/.venv/bin/python" -c "import PyQt6" 2>/dev/null; then
    "$ROOT/.venv/bin/pip" install -q PyQt6
fi
ok "PyQt6 $("$ROOT/.venv/bin/python" -c 'import PyQt6.QtCore as c; print(c.QT_VERSION_STR)')"

echo "==> [3/6] libxcb-cursor（Qt xcb 插件依赖）"
if ldconfig -p 2>/dev/null | grep -q libxcb-cursor; then
    ok "系统已安装"
elif [ -f "$ROOT/vendor/lib/libxcb-cursor.so.0" ]; then
    ok "vendor/ 已存在"
else
    echo "    系统缺失，下载 .deb 解压到 vendor/（无需 root）..."
    TMP="$(mktemp -d)"
    if (cd "$TMP" && apt download libxcb-cursor0 >/dev/null 2>&1 && dpkg-deb -x libxcb-cursor0_*.deb extracted); then
        mkdir -p "$ROOT/vendor/lib"
        cp "$TMP"/extracted/usr/lib/*/libxcb-cursor.so* "$ROOT/vendor/lib/"
        ok "vendor/lib/（无需 root 的本地副本）"
    else
        warn "自动下载失败。请手动执行: sudo apt install libxcb-cursor0"
    fi
    rm -rf "$TMP"
fi

echo "==> [4/6] cloudflared（手机公网访问隧道，可选但推荐）"
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64) CF_ASSET="cloudflared-linux-amd64" ;;
    aarch64|arm64) CF_ASSET="cloudflared-linux-arm64" ;;
    *) CF_ASSET="" ;;
esac
if command -v cloudflared >/dev/null; then
    ok "系统已安装: $(command -v cloudflared)"
elif [ -x "$ROOT/vendor/bin/cloudflared" ]; then
    ok "vendor/bin/ 已存在"
elif [ -z "$CF_ASSET" ]; then
    warn "不支持的架构 $ARCH，跳过（仅局域网访问，不影响其他功能）"
else
    echo "    下载 cloudflared（免 root，用于手机 4G/外出访问）..."
    mkdir -p "$ROOT/vendor/bin"
    if curl -fsSL -o "$ROOT/vendor/bin/cloudflared" \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/$CF_ASSET"; then
        chmod +x "$ROOT/vendor/bin/cloudflared"
        ok "vendor/bin/cloudflared"
    else
        warn "下载失败（仅局域网访问，不影响其他功能）；可稍后重跑 install.sh"
    fi
fi

echo "==> [5/6] 桌面入口"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$APPS" "$ICONS"
cp "$ROOT/assets/codex-quota.svg" "$ICONS/codex-quota.svg"
cat > "$APPS/codex-quota.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Codex Quota
Comment=Codex/Kimi 额度悬浮窗 / AI quota floating widget
Exec=$ROOT/bin/codex-quota
Icon=codex-quota
Terminal=false
Categories=Utility;Development;
Keywords=codex;kimi;quota;token;
StartupWMClass=codex-quota
EOF
ok "$APPS/codex-quota.desktop（含图标）"

echo "==> [6/6] 命令入口（~/.local/bin）"
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
ln -sf "$ROOT/bin/codex-quota" "$BIN_DIR/codex-quota"
ok "$BIN_DIR/codex-quota -> $ROOT/bin/codex-quota"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "$BIN_DIR 不在 PATH 里，终端直接敲 codex-quota 会找不到。"
    warn "  加入 PATH: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
    warn "  （应用菜单启动、开机自启不受影响）"
fi

echo
echo "安装完成！启动方式（任选）："
echo "  · 终端: codex-quota"
echo "  · 应用菜单搜索 \"Codex Quota\"（黄色闪电图标）点击启动"
echo "  · 开机自启: 启动后在托盘菜单勾选 \"开机自启\""
echo "  · 已在运行时重复启动只会提示，不会开第二个实例"
echo
echo "日志: ~/.cache/codex-quota/hud.log"

# 交互终端下装完直接问要不要启动，不用记任何启动方式
if [ -t 0 ] && [ -t 1 ]; then
    echo
    read -r -p "现在启动 Codex Quota？[Y/n] " ans || true
    case "${ans:-Y}" in
        n|N) echo "稍后想启动时终端敲 codex-quota 即可。" ;;
        *) "$ROOT/bin/codex-quota" ;;
    esac
fi
