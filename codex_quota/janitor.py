"""孤儿进程清扫：上次异常退出（kill -9 / 断电 / 崩溃）遗留的本应用子进程。

匹配特征（只杀我们明确标识的，绝不误伤用户自己的进程）：
- cloudflared：vendor 路径 或 （--url 127.0.0.1 + --no-autoupdate 组合特征）
- kimi web：命令行含 --no-open（用户手动跑 kimi web 不会带这个参数）
"""

from __future__ import annotations

import logging
import os
import signal
from typing import Callable, Optional

logger = logging.getLogger("codex_quota.janitor")


def is_our_cloudflared(cmdline: list[str]) -> bool:
    joined = " ".join(cmdline)
    if "vendor/bin/cloudflared" in joined:
        return True
    return ("cloudflared" in os.path.basename(cmdline[0] if cmdline else "")
            and "--no-autoupdate" in cmdline
            and "--url" in cmdline
            and any("127.0.0.1" in a for a in cmdline))


def is_our_kimi_web(cmdline: list[str]) -> bool:
    return "--no-open" in cmdline and "web" in cmdline


def _read_cmdline(pid: int) -> Optional[list[str]]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            parts = f.read().split(b"\0")
        return [p.decode(errors="replace") for p in parts if p]
    except OSError:
        return None


def cleanup_orphans(*, list_pids: Optional[Callable[[], list[int]]] = None,
                    read_cmdline: Optional[Callable[[int], Optional[list[str]]]] = None,
                    kill: Optional[Callable[[int], None]] = None) -> int:
    """清理孤儿，返回清理数量。参数可注入以便测试。"""
    if list_pids is None:
        list_pids = lambda: [int(d) for d in os.listdir("/proc") if d.isdigit()]
    read_cmdline = read_cmdline or _read_cmdline
    if kill is None:
        def kill(pid: int) -> None:
            os.kill(pid, signal.SIGTERM)

    killed = 0
    self_pid = os.getpid()
    for pid in list_pids():
        if pid == self_pid:
            continue
        cmd = read_cmdline(pid)
        if not cmd:
            continue
        if is_our_cloudflared(cmd) or is_our_kimi_web(cmd):
            try:
                kill(pid)
                killed += 1
                logger.info("清理遗留进程: pid=%d %s", pid, os.path.basename(cmd[0]))
            except (ProcessLookupError, PermissionError):
                pass
    return killed
