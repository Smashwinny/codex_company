#!/usr/bin/env bash
# codex-quota 安装/修复脚本：
#   1. 准备 .venv（PyQt6）
#   2. 确保 libxcb-cursor 可用（无 root 时下载 .deb 解压到 vendor/）
#   3. 创建应用菜单桌面入口（~/.local/share/applications/codex-quota.desktop）
# 之后可从应用菜单搜索 "Codex Quota" 启动，或运行 bin/codex-quota。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "==> [1/3] Python 环境"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
    if command -v virtualenv >/dev/null; then
        virtualenv .venv
    else
        python3 -m venv .venv
    fi
fi
"$ROOT/.venv/bin/pip" show PyQt6 >/dev/null 2>&1 || "$ROOT/.venv/bin/pip" install PyQt6
echo "    OK: $("$ROOT/.venv/bin/python" -c 'import PyQt6.QtCore; print("PyQt6", PyQt6.QtCore.QT_VERSION_STR)')"

echo "==> [2/3] libxcb-cursor"
if ldconfig -p 2>/dev/null | grep -q libxcb-cursor; then
    echo "    OK: 系统已安装"
elif [ -f "$ROOT/vendor/lib/libxcb-cursor.so.0" ]; then
    echo "    OK: vendor/ 已存在"
else
    echo "    系统缺失且无 root，下载 .deb 解压到 vendor/ ..."
    TMP="$(mktemp -d)"
    (cd "$TMP" && apt download libxcb-cursor0 >/dev/null 2>&1 && dpkg-deb -x libxcb-cursor0_*.deb extracted)
    mkdir -p "$ROOT/vendor/lib"
    cp "$TMP"/extracted/usr/lib/*/libxcb-cursor.so* "$ROOT/vendor/lib/"
    rm -rf "$TMP"
    echo "    OK: vendor/lib/"
fi

echo "==> [3/3] 桌面入口"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/codex-quota.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Codex Quota
Comment=Codex 额度悬浮窗 / Codex quota floating widget
Exec=$ROOT/bin/codex-quota
Terminal=false
Categories=Utility;Development;
Keywords=codex;quota;token;
EOF
echo "    OK: $APPS/codex-quota.desktop"

echo
echo "完成！启动方式（任选）："
echo "  · 应用菜单搜索 \"Codex Quota\""
echo "  · $ROOT/bin/codex-quota"
echo "  · 开机自启：启动后在托盘菜单勾选\"开机自启\""
