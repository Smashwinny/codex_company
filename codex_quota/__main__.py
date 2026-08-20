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

    from PyQt6.QtCore import QTimer

    # 日志进 stderr → 启动器重定向到 ~/.cache/codex-quota/hud.log
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%m-%d %H:%M:%S",
    )

    from .providers import default_providers
    from .ui import FloatingHud

    # 清扫上轮异常退出遗留的子进程（kill -9/断电场景兜底）
    from .janitor import cleanup_orphans

    swept = cleanup_orphans()
    if swept:
        logging.getLogger("codex_quota").info("清理上轮遗留进程 %d 个", swept)

    app = QApplication([sys.argv[0], *args])
    app.setApplicationName("codex-quota")
    providers = default_providers()
    hud = FloatingHud(providers)
    hud.restore_position()
    settings = hud._settings

    # 告警阈值（黄线/红线）：settings.json 或托盘菜单"告警阈值"可调
    from .ui.widgets import set_thresholds

    try:
        set_thresholds(float(settings.get("color_warn_threshold")),
                       float(settings.get("color_crit_threshold")))
    except (TypeError, ValueError) as exc:
        logging.getLogger("codex_quota").warning("阈值配置无效，用默认值: %s", exc)

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
    new_ntfy_topic = False
    if settings.get("notify_enabled"):
        from .notify import NtfyNotifier
        from .web import generate_token

        topic = settings.get("ntfy_topic")
        if not topic:
            topic = "codex-quota-" + generate_token()[:12]
            settings.set("ntfy_topic", topic)
            new_ntfy_topic = True
        hud.notifier = NtfyNotifier(server=settings.get("ntfy_server"), topic=topic)
        print(f"手机通知: ntfy App 订阅主题 {topic}（{hud.notifier.subscribe_url}）",
              file=sys.stderr)

    # 启动即把访问地址推到 ntfy（隧道 URL 每次随机，不推就没法在手机上主动找到）；
    # 手机点通知直接打开网页。之后想再要地址可从托盘菜单"推送访问地址到手机"重发
    if hud.notifier is not None:
        url = hud.public_url or hud.web_url
        if url:
            hud.notifier.publish(
                "codex-quota",
                f"📱 手机访问地址（点通知直接打开）：\n{url}",
                tags="link", click=url)

    # 手机反向触发：向 <主题>-cmd 发 "url" → 回推当前访问地址（点通知直达网页）。
    # 回调读取的是触发时刻的 hud.public_url，隧道重连换新地址后也始终推最新值
    cmd_listener = None
    if hud.notifier is not None and (hud.public_url or hud.web_url):
        from .notify import NtfyCommandListener

        def _push_url_on_demand():
            url = hud.public_url or hud.web_url
            if url:
                hud.notifier.publish(
                    "codex-quota",
                    f"📱 手机访问地址（点通知直接打开）：\n{url}",
                    tags="link", click=url)

        cmd_topic = hud.notifier.topic + "-cmd"
        cmd_listener = NtfyCommandListener(hud.notifier.server, cmd_topic,
                                           _push_url_on_demand)
        cmd_listener.start()
        print(f"手机触发推送: ntfy 向主题 {cmd_topic} 发送 url 即回推访问地址",
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

    # 首次生成 ntfy 主题时自动弹一次订阅指引：订阅关系刚建立，
    # 正是用户最需要"手机上要做什么"的时刻；之后从托盘菜单再开
    if new_ntfy_topic and hud.notifier is not None:
        from .ui.notify_dialog import NotifyGuideDialog

        NotifyGuideDialog(hud.notifier, parent=hud).exec()

    # 隧道看门狗：cloudflared 死亡 → 限流自动重连 → ntfy 推送新地址
    if tunnel is not None:
        import threading

        from .tunnel import RestartPolicy

        _policy = RestartPolicy()
        _log = logging.getLogger("codex_quota")
        _restart_busy = threading.Event()

        def _restart_tunnel():
            try:
                _log.warning("cloudflared 已退出，尝试自动重连")
                if not _policy.allow():
                    _log.warning("隧道重连过于频繁，进入冷却（10 分钟内最多 5 次）")
                    return
                try:
                    base = tunnel.start()
                except Exception as exc:
                    _log.warning("隧道重连失败: %s", exc)
                    return
                hud.public_url = f"{base}/t/{token}/"
                print(f"手机访问(公网): {hud.public_url}", file=sys.stderr)
                if hud.notifier is not None:
                    hud.notifier.publish(
                        "codex-quota",
                        f"📱 手机访问新地址（隧道已重连）：\n{hud.public_url}",
                        tags="link", click=hud.public_url)
            finally:
                _restart_busy.clear()

        def _check_tunnel():
            # start() 会阻塞数秒，放后台线程避免卡 UI
            if tunnel.is_alive() or _restart_busy.is_set():
                return
            _restart_busy.set()
            threading.Thread(target=_restart_tunnel, daemon=True).start()

        _tunnel_watchdog = QTimer()
        _tunnel_watchdog.setInterval(30_000)
        _tunnel_watchdog.timeout.connect(_check_tunnel)
        _tunnel_watchdog.start()
        hud._tunnel_watchdog = _tunnel_watchdog  # 防 GC

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
    _sig_timer = QTimer()
    _sig_timer.start(500)
    _sig_timer.timeout.connect(lambda: None)
    try:
        return app.exec()
    finally:
        if cmd_listener is not None:
            cmd_listener.stop()
        if tunnel is not None:
            tunnel.stop()
        if web_server is not None:
            web_server.stop()
        for p in providers:
            p.close()  # 释放 kimi web 等保活进程


if __name__ == "__main__":
    sys.exit(main())
