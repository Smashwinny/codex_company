"""cloudflared Quick Tunnel：把本地 Web 服务暴露为公网 HTTPS 地址。

- 免 root 免注册：单二进制（install.sh 下载到 vendor/bin/）
- spawn `cloudflared tunnel --url http://127.0.0.1:<port>`，
  从 stderr 解析 https://<random>.trycloudflare.com
- 地址是临时的：每次隧道重启都会变（托盘菜单永远展示当前有效地址，
  需要固定域名可后续接 Cloudflare 账号的 Named Tunnel）
- 安全：隧道只转发到本地 Web 服务，鉴权仍由 URL 里的 token 兜底
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Optional

TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelError(Exception):
    pass


def find_cloudflared() -> Optional[str]:
    """定位 cloudflared：PATH → 项目 vendor/bin/。找不到返回 None（仅局域网模式）。"""
    found = shutil.which("cloudflared")
    if found:
        return found
    vendor = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "vendor", "bin", "cloudflared")
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

    def start(self) -> str:
        if self._bin is None:
            raise TunnelError("未找到 cloudflared（运行 install.sh 会自动下载）")
        self.stop()
        self._proc = subprocess.Popen(
            [self._bin, "tunnel", "--url", f"http://127.0.0.1:{self._local_port}",
             "--no-autoupdate"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,  # 地址打在 stderr
            text=True,
            start_new_session=True,
        )
        lines: queue.Queue[str] = queue.Queue()

        def _pump() -> None:
            assert self._proc is not None and self._proc.stderr is not None
            for line in self._proc.stderr:
                lines.put(line)

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
                return self.public_url
        self.stop()
        raise TunnelError("cloudflared 启动超时或未输出公网地址")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        self.public_url = None
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
