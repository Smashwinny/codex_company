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

    import logging

    # 日志进 stderr → 启动器重定向到 ~/.cache/codex-quota/hud.log
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%m-%d %H:%M:%S",
    )

    from .providers import default_providers
    from .ui import FloatingHud

    app = QApplication([sys.argv[0], *args])
    app.setApplicationName("codex-quota")
    providers = default_providers()
    hud = FloatingHud(providers)
    hud.restore_position()
    settings = hud._settings

    # 首启向导：环境检测 + 修复引导（先于 web/隧道/通知初始化，勾选结果即生效）
    from .ui.wizard import SetupWizardDialog, should_show_wizard

    if should_show_wizard(settings):
        SetupWizardDialog(settings, parent=hud).exec()

    # 手机访问：局域网 Web 服务（token 在 URL 里鉴权）+ 可选公网隧道
    web_server = None
    tunnel = None
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

    # 额度重置推送：ntfy 主题（主题即凭证，自动生成持久化）
    if settings.get("notify_enabled"):
        from .notify import NtfyNotifier
        from .web import generate_token

        topic = settings.get("ntfy_topic")
        if not topic:
            topic = "codex-quota-" + generate_token()[:12]
            settings.set("ntfy_topic", topic)
        hud.notifier = NtfyNotifier(server=settings.get("ntfy_server"), topic=topic)
        print(f"手机通知: ntfy App 订阅主题 {topic}（{hud.notifier.subscribe_url}）",
              file=sys.stderr)

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
    # 优雅退出：SIGTERM/SIGINT → 正常退出事件循环，finally 回收子进程
    # （kimi web / cloudflared 都在独立进程组，主进程被杀不会连带，必须主动清理）
    import signal

    def _graceful_quit(signum, frame):
        logging.getLogger("codex_quota").info("收到信号 %s，正在退出", signum)
        QApplication.quit()

    signal.signal(signal.SIGTERM, _graceful_quit)
    signal.signal(signal.SIGINT, _graceful_quit)
    # Python 信号处理器只在解释器执行字节码时运行，app.exec() 阻塞在 C++ 层；
    # 用空 QTimer 周期唤醒解释器，让挂起的信号处理器得以及时执行
    from PyQt6.QtCore import QTimer

    _sig_timer = QTimer()
    _sig_timer.start(500)
    _sig_timer.timeout.connect(lambda: None)
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
