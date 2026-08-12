"""codex-quota: Linux 桌面端 Codex 额度监控。

默认启动悬浮窗（HUD）；`--cli` 进入 CLI 模式。
"""

from __future__ import annotations

import sys


def main() -> int:
    args = sys.argv[1:]
    if "--cli" in args:
        args.remove("--cli")
        from .cli import run_cli

        return run_cli(args)
    return _run_hud(args)


def _run_hud(args: list[str]) -> int:
    try:
        from PyQt6.QtWidgets import QApplication, QSystemTrayIcon
    except ImportError:
        print(
            "悬浮窗模式需要 PyQt6：pip install PyQt6\n"
            "或使用 CLI 模式：python -m codex_quota --cli",
            file=sys.stderr,
        )
        return 69  # EX_UNAVAILABLE

    from .ui import FloatingHud

    app = QApplication([sys.argv[0], *args])
    app.setApplicationName("codex-quota")
    hud = FloatingHud()
    hud.restore_position()

    if QSystemTrayIcon.isSystemTrayAvailable():
        from .ui.tray import QuotaTray

        # 有托盘：关窗只是隐藏，从托盘菜单退出
        app.setQuitOnLastWindowClosed(False)
        tray = QuotaTray(hud, app)
        tray.show()
    else:
        # GNOME 默认无托盘（需 AppIndicator 扩展）：关窗即退出
        print("提示：未检测到系统托盘，仅运行悬浮窗（关窗即退出）。", file=sys.stderr)

    hud.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
