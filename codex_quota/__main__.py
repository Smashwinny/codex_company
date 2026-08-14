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

    from .providers import default_providers
    from .ui import FloatingHud

    app = QApplication([sys.argv[0], *args])
    app.setApplicationName("codex-quota")
    providers = default_providers()
    hud = FloatingHud(providers)
    hud.restore_position()

    # 手机访问：局域网 Web 服务（token 在 URL 里鉴权）+ 可选公网隧道
    web_server = None
    tunnel = None
    settings = hud._settings
    if settings.get("web_enabled"):
        from .web import WebServer, generate_token

        token = settings.get("web_token")
        if not token:
            token = generate_token()
            settings.set("web_token", token)
        web_server = WebServer(hud._current_views,
                               port=int(settings.get("web_port")), token=token)
        try:
            web_server.start()
            hud.web_url = web_server.url
            print(f"手机访问(局域网): {hud.web_url}", file=sys.stderr)
        except OSError as exc:
            print(f"Web 服务启动失败（不影响悬浮窗）: {exc}", file=sys.stderr)
            web_server = None

        # 公网隧道：任意网络（4G/外出）可访问；cloudflared 缺失时仅局域网
        if web_server is not None and settings.get("tunnel_enabled"):
            from .tunnel import Tunnel, TunnelError

            tunnel = Tunnel(web_server.port)
            try:
                public_base = tunnel.start()
                hud.public_url = f"{public_base}/t/{token}/"
                print(f"手机访问(公网): {hud.public_url}", file=sys.stderr)
            except TunnelError as exc:
                print(f"公网隧道不可用（仅局域网访问）: {exc}", file=sys.stderr)
                tunnel = None

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
    try:
        return app.exec()
    finally:
        if tunnel is not None:
            tunnel.stop()
        if web_server is not None:
            web_server.stop()
        for p in providers:
            p.close()  # 释放 kimi web 等保活进程


if __name__ == "__main__":
    sys.exit(main())
