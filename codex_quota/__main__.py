"""codex-quota: 跨平台多 provider 额度监控。

默认启动悬浮窗（HUD）；`--cli` 进入 CLI 模式。
"""

from __future__ import annotations

import sys


def _start_tunnel_guarded(tunnel, stopping, gate):
    """与退出置位原子互斥：stopping 生效后不得再 spawn cloudflared。"""
    with gate:
        if stopping.is_set():
            return None
        return tunnel.start()


def _quiesce_tunnel_restart(watchdog, stopping, gate, thread) -> None:
    """关闭重连入口并等待在途 worker；调用者随后才能 stop tunnel/删 pidfile。"""
    if watchdog is not None:
        watchdog.stop()
    if stopping is not None and gate is not None:
        with gate:
            stopping.set()
    if thread is not None and thread.is_alive():
        thread.join()


def main() -> int:
    args = sys.argv[1:]
    if "--cli" in args:
        args.remove("--cli")
        from .cli import run_cli

        return run_cli(args)
    return _run_hud(args)


def _run_hud(args: list[str]) -> int:
    # Windows pythonw.exe 下 sys.stdout/sys.stderr 都是 None——不先接到日志文件，
    # 任何 print(..., file=sys.stderr)/logging 都 AttributeError，且无控制台无
    # 日志，表现为"静默起不来"。这是 Windows 兼容里最高优先的一处。
    import os

    if sys.stderr is None:
        from .sysdirs import cache_dir, log_path

        os.makedirs(cache_dir(), exist_ok=True)
        sys.stdout = sys.stderr = open(log_path(), "a", encoding="utf-8",
                                       buffering=1)
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

    # 日志进 stderr → 启动器重定向（POSIX）或上面的守卫（pythonw）写 hud.log
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

    # 单实例必须最先判定（QApplication 之后、其余一切之前）：
    # 第二实例通知已有实例 raise 窗口后立即退出——否则它会先跑 janitor
    # 把第一实例正在运行的 cloudflared/kimi-web 当"孤儿"杀掉，还会
    # 重复占端口、起第二条隧道
    from .single_instance import SingleInstance

    single = SingleInstance()
    if not single.try_acquire():
        return 0

    # 记录主进程 PID（.cmd 启动器/未来 exe 的存活探测用）；过了单实例判定
    # 才写，第二实例不覆盖第一实例的 app.pid
    from .sysdirs import cache_dir

    app_pid_path = os.path.join(cache_dir(), "app.pid")
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        with open(app_pid_path, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        app_pid_path = ""  # 写不进就不提供该能力

    # 清扫上轮异常退出遗留的子进程（kill -9/断电场景兜底）。
    # 过了单实例判定才来清扫：此刻必然没有其他存活的实例
    from .janitor import cleanup_orphans

    swept = cleanup_orphans()
    if swept:
        logging.getLogger("codex_quota").info("清理上轮遗留进程 %d 个", swept)

    providers = default_providers()
    hud = FloatingHud(providers)
    hud.restore_position()
    settings = hud._settings
    single.set_raise_callback(
        lambda: (hud.show(), hud.raise_(), hud.activateWindow()))

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
    tunnel_watchdog = None
    tunnel_restart_stop = None
    tunnel_restart_gate = None
    tunnel_restart_thread = None
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

    # 手机反向触发：向 <主题>-cmd 发命令 → 回推结果（点通知直达网页）。
    # url=要地址；列表=看重置提醒开关；kimi5/spark 等关键词=切换对应提醒。
    # 地址类回调读取的是触发时刻的 hud.public_url，隧道重连后始终推最新值
    cmd_listener = None
    if hud.notifier is not None and (hud.public_url or hud.web_url):
        from .notify import NtfyCommandListener
        from .remote_cmd import handle_command

        def _on_phone_command(msg: str):
            body, click = handle_command(msg, hud._current_views(), settings,
                                         url=hud.public_url or hud.web_url)
            if body:
                hud.notifier.publish("codex-quota", body, tags="link", click=click)

        cmd_topic = hud.notifier.topic + "-cmd"
        cmd_listener = NtfyCommandListener(hud.notifier.server, cmd_topic,
                                           _on_phone_command)
        cmd_listener.start()
        print(f"手机远程命令: ntfy 向主题 {cmd_topic} 发送 "
              f"url（要地址）/ 列表（看提醒开关）/ kimi5 等关键词（切换提醒）",
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
        tunnel_restart_stop = threading.Event()
        tunnel_restart_gate = threading.Lock()

        def _restart_tunnel():
            try:
                if tunnel_restart_stop.is_set():
                    return
                _log.warning("cloudflared 已退出，尝试自动重连")
                if not _policy.allow():
                    _log.warning("隧道重连过于频繁，进入冷却（10 分钟内最多 5 次）")
                    return
                try:
                    base = _start_tunnel_guarded(
                        tunnel, tunnel_restart_stop, tunnel_restart_gate)
                except Exception as exc:
                    _log.warning("隧道重连失败: %s", exc)
                    return
                if base is None or tunnel_restart_stop.is_set():
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
            nonlocal tunnel_restart_thread
            # start() 会阻塞数秒，放后台线程避免卡 UI
            if (tunnel_restart_stop.is_set() or tunnel.is_alive()
                    or _restart_busy.is_set()):
                return
            _restart_busy.set()
            tunnel_restart_thread = threading.Thread(
                target=_restart_tunnel, daemon=True,
                name="cloudflared-restart")
            tunnel_restart_thread.start()

        tunnel_watchdog = QTimer()
        tunnel_watchdog.setInterval(30_000)
        tunnel_watchdog.timeout.connect(_check_tunnel)
        tunnel_watchdog.start()
        hud._tunnel_watchdog = tunnel_watchdog  # 防 GC

    # 优雅退出：SIGTERM/SIGINT → 正常退出事件循环，finally 回收子进程
    # （kimi web / cloudflared 都在独立进程组，主进程被杀不会连带，必须主动清理）
    import signal

    def _graceful_quit(signum, frame):
        logging.getLogger("codex_quota").info("收到信号 %s，正在退出", signum)
        QApplication.quit()

    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _graceful_quit)
        except (ValueError, OSError, AttributeError, RuntimeError):
            pass  # Windows 无 POSIX 信号语义，注册失败不致命
    # Python 信号处理器只在解释器执行字节码时运行，app.exec() 阻塞在 C++ 层；
    # 用空 QTimer 周期唤醒解释器，让挂起的信号处理器得以及时执行
    _sig_timer = QTimer()
    _sig_timer.start(500)
    _sig_timer.timeout.connect(lambda: None)
    try:
        return app.exec()
    finally:
        # 先原子关闭重连入口，再等待已在途的 start() 完成；这样后面的 stop()
        # 与 children.pid 删除之后，绝不会有后台线程重新 spawn cloudflared。
        _quiesce_tunnel_restart(
            tunnel_watchdog, tunnel_restart_stop,
            tunnel_restart_gate, tunnel_restart_thread)
        if cmd_listener is not None:
            cmd_listener.stop()
        if tunnel is not None:
            tunnel.stop()
        if web_server is not None:
            web_server.stop()
        for p in providers:
            p.close()  # 释放 kimi web 等保活进程
        # 子进程已全部回收，清掉 pidfile——正常退出的实例不留"孤儿"记录，
        # 下次启动 sweep 只会命中真正被强杀遗留的进程
        try:
            os.remove(os.path.join(cache_dir(), "children.pid"))
        except OSError:
            pass
        if app_pid_path:
            try:
                os.remove(app_pid_path)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
