"""Codex app-server JSON-RPC 客户端。

数据源：启动 `codex app-server` 子进程，经 stdin/stdout 走 JSON-RPC，
调用只读方法 account/rateLimits/read。不读 auth.json、不碰凭证，
认证由 codex CLI 自身完成。该协议为 experimental，解析层全字段容错。

参考实现：ai-fuelgauge（协议交互）、codex-usage-monitor（查完即释放进程）。
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_TIMEOUT = 8.0  # 秒；超时即 kill 子进程
CLIENT_INFO = {"name": "codex-quota", "version": "0.1.0"}


class AppServerError(Exception):
    """查询失败的统一错误类型，message 面向用户可读。"""


class CodexNotFoundError(AppServerError):
    pass


@dataclass
class QuotaWindow:
    """单个限流窗口。字段缺失时全部为 None（未知），与 0（已用 0%）严格区分。"""

    used_percent: Optional[float] = None
    window_minutes: Optional[int] = None
    reset_at: Optional[float] = None  # Unix 秒

    @property
    def remaining_percent(self) -> Optional[float]:
        """剩余额度百分比（展示层统一用这个）。"""
        if self.used_percent is None:
            return None
        return max(0.0, 100.0 - self.used_percent)

    @property
    def label(self) -> str:
        """按 windowDurationMins 归一化窗口名称。"""
        m = self.window_minutes
        if m is None:
            return "窗口"
        if m <= 360:
            return "5小时"
        if m >= 5000:
            return "本周"
        return f"{m / 60:.0f}小时"

    def reset_in_seconds(self, now: Optional[float] = None) -> Optional[float]:
        if self.reset_at is None:
            return None
        return self.reset_at - (now if now is not None else time.time())


@dataclass
class Credits:
    has_credits: bool = False
    unlimited: bool = False
    balance: Optional[str] = None


@dataclass
class RateLimit:
    limit_id: str
    limit_name: Optional[str] = None
    plan_type: Optional[str] = None
    primary: QuotaWindow = field(default_factory=QuotaWindow)
    secondary: Optional[QuotaWindow] = None
    credits: Optional[Credits] = None


@dataclass
class QuotaSnapshot:
    """一次查询的完整结果。fetched_at 为本地 Unix 秒。"""

    fetched_at: float
    plan_type: Optional[str]
    limits: list[RateLimit]  # 主限额在前，附加限额桶（如 Spark）在后

    @property
    def primary_limit(self) -> Optional[RateLimit]:
        return self.limits[0] if self.limits else None


def find_codex_bin() -> str:
    """定位 codex 可执行文件；CODEX_BIN 环境变量可覆盖。"""
    override = os.environ.get("CODEX_BIN")
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        raise CodexNotFoundError(f"CODEX_BIN 指向的文件不可执行: {override}")
    path = shutil.which("codex")
    if path is None:
        raise CodexNotFoundError(
            "未找到 codex 可执行文件。请先安装 Codex CLI 并登录（codex login），"
            "或设置 CODEX_BIN 环境变量。"
        )
    return path


def _parse_window(raw: Any) -> Optional[QuotaWindow]:
    if not isinstance(raw, dict):
        return None
    used = raw.get("usedPercent")
    mins = raw.get("windowDurationMins")
    resets = raw.get("resetsAt")
    return QuotaWindow(
        # 用 is-not-None 判断，保留合法的 0 值
        used_percent=float(used) if used is not None else None,
        window_minutes=int(mins) if mins is not None else None,
        reset_at=float(resets) if isinstance(resets, (int, float)) else None,
    )


def _parse_credits(raw: Any) -> Optional[Credits]:
    if not isinstance(raw, dict):
        return None
    return Credits(
        has_credits=bool(raw.get("hasCredits")),
        unlimited=bool(raw.get("unlimited")),
        balance=raw.get("balance") if raw.get("balance") is not None else None,
    )


def _parse_limit(raw: dict[str, Any]) -> RateLimit:
    return RateLimit(
        limit_id=raw.get("limitId") or "?",
        limit_name=raw.get("limitName"),
        plan_type=raw.get("planType"),
        primary=_parse_window(raw.get("primary")) or QuotaWindow(),
        secondary=_parse_window(raw.get("secondary")),
        credits=_parse_credits(raw.get("credits")),
    )


def parse_rate_limits_response(result: dict[str, Any], now: Optional[float] = None) -> QuotaSnapshot:
    """把 account/rateLimits/read 的 result 块归一化为 QuotaSnapshot。

    主限额来自顶层 rateLimits；rateLimitsByLimitId 中与主 limitId 不同的条目
    作为附加限额桶追加（如 GPT-5.3-Codex-Spark）。
    """
    fetched_at = now if now is not None else time.time()
    rl = result.get("rateLimits")
    if not isinstance(rl, dict):
        raise AppServerError("app-server 返回中缺少 rateLimits 数据")

    limits: list[RateLimit] = []
    main = _parse_limit(rl)
    limits.append(main)

    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        for limit_id, entry in by_id.items():
            if limit_id == main.limit_id or not isinstance(entry, dict):
                continue
            limits.append(_parse_limit(entry))

    return QuotaSnapshot(fetched_at=fetched_at, plan_type=main.plan_type, limits=limits)


class AppServerClient:
    """每次查询独立 spawn 一个 app-server，查完即 terminate，避免进程泄漏。"""

    def __init__(self, codex_bin: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT):
        self.codex_bin = codex_bin or find_codex_bin()
        self.timeout = timeout

    def read_rate_limits(self) -> QuotaSnapshot:
        proc = subprocess.Popen(
            [self.codex_bin, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        lines: queue.Queue[str] = queue.Queue()

        def _pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)

        reader = threading.Thread(target=_pump, daemon=True)
        reader.start()
        try:
            self._send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"clientInfo": CLIENT_INFO}})
            self._send(proc, {"jsonrpc": "2.0", "id": 2,
                              "method": "account/rateLimits/read", "params": {}})
            result = self._await_response(lines, want_id=2)
        finally:
            self._reap(proc)

        if "error" in result:
            raise AppServerError(f"app-server 返回错误: {result['error']}")
        payload = result.get("result")
        if not isinstance(payload, dict):
            raise AppServerError("app-server 响应格式异常（缺少 result）")
        return parse_rate_limits_response(payload)

    @staticmethod
    def _send(proc: subprocess.Popen, msg: dict[str, Any]) -> None:
        assert proc.stdin is not None
        try:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AppServerError(f"无法向 app-server 写入请求: {exc}") from exc

    def _await_response(self, lines: queue.Queue[str], want_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerError(f"app-server 响应超时（{self.timeout:.0f} 秒）")
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                raise AppServerError(f"app-server 响应超时（{self.timeout:.0f} 秒）")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # 跳过非 JSON 的日志行
            if msg.get("id") == want_id:
                return msg

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)
