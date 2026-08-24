"""cloudflared Quick Tunnel：把本地 Web 服务暴露为公网 HTTPS 地址。

- 免 root 免注册：单二进制（install.sh 下载到 vendor/bin/）
- spawn `cloudflared tunnel --url http://127.0.0.1:<port>`，
  从 stderr 解析 https://<random>.trycloudflare.com
- 地址是临时的：每次隧道重启都会变（托盘菜单永远展示当前有效地址，
  需要固定域名可后续接 Cloudflare 账号的 Named Tunnel）
- 安全：隧道只转发到本地 Web 服务，鉴权仍由 URL 里的 token 兜底
"""

from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Optional

from . import proc

TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

logger = logging.getLogger("codex_quota.tunnel")


class TunnelError(Exception):
    pass


class RestartPolicy:
    """隧道重启限流：滑动窗口内最多 max_attempts 次，防止断网期间疯狂重试。"""

    def __init__(self, max_attempts: int = 5, window_s: float = 600.0):
        self._max = max_attempts
        self._window = window_s
        self._times: list[float] = []

    def allow(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.monotonic()
        self._times = [t for t in self._times if now - t < self._window]
        if len(self._times) >= self._max:
            return False
        self._times.append(now)
        return True


def find_cloudflared() -> Optional[str]:
    """定位 cloudflared：PATH → 项目 vendor/bin/（Windows 为 cloudflared.exe）。
    找不到返回 None（仅局域网模式）。"""
    found = shutil.which("cloudflared")
    if found:
        return found
    name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
    vendor = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "vendor", "bin", name)
    return vendor if os.path.isfile(vendor) else None


class Tunnel:
    """cloudflared 进程生命周期管理。start() 成功后从 public_url 取地址。"""

    def __init__(self, local_port: int, *, cloudflared_bin: Optional[str] = None,
                 startup_timeout: float = 30.0):
        self._bin = cloudflared_bin or find_cloudflared()
        self._local_port = local_port
        self._startup_timeout = startup_timeout
        self._proc: Optional[subprocess.Popen] = None
        self.public_url: Optional[str] = None

    @property
    def available(self) -> bool:
        return self._bin is not None

    def is_alive(self) -> bool:
        """隧道进程是否存活（看门狗用）。"""
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> str:
        if self._bin is None:
            raise TunnelError("未找到 cloudflared（运行 install.sh 会自动下载）")
        self.stop()
        self._proc = proc.spawn_detached(
            [self._bin, "tunnel", "--url", f"http://127.0.0.1:{self._local_port}",
             "--no-autoupdate"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,  # 地址打在 stderr
            text=True,
        )
        proc.record_child(self._proc.pid, "cloudflared")
        assert self._proc.stderr is not None
        # Windows 的 locale 常为 GBK，但 cloudflared 的结构化日志固定输出 UTF-8；
        # 显式重配文本管道并容错异常字节，避免 pump 线程因解码错误退出。
        self._proc.stderr.reconfigure(encoding="utf-8", errors="replace")
        lines: queue.Queue[str] = queue.Queue()

        def _pump() -> None:
            assert self._proc is not None and self._proc.stderr is not None
            for line in self._proc.stderr:
                if " ERR " in line or line.startswith("ERR"):
                    logger.warning("cloudflared: %s", line.strip())
                lines.put(line)
            # stderr 关闭 = 进程退出；若非主动 stop，说明隧道意外断了
            if self._proc is not None:
                logger.warning("cloudflared 进程意外退出，公网地址已失效")

        threading.Thread(target=_pump, daemon=True).start()

        deadline = time.monotonic() + self._startup_timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                break
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            m = TUNNEL_URL_RE.search(line)
            if m:
                self.public_url = m.group(0)
                logger.info("cloudflared 隧道已建立: %s", self.public_url)
                return self.public_url
        self.stop()
        raise TunnelError("cloudflared 启动超时或未输出公网地址")

    def stop(self) -> None:
        proc_, self._proc = self._proc, None
        self.public_url = None
        if proc_ is not None:
            proc.kill_tree(proc_, timeout=3)
            if proc_.poll() is not None:
                proc.forget_child(proc_.pid)
