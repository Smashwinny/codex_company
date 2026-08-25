"""Codex app-server JSON-RPC 客户端。

数据源：启动 `codex app-server` 子进程，经 stdin/stdout 走 JSON-RPC，
调用只读方法 account/rateLimits/read。不读 auth.json、不碰凭证，
认证由 codex CLI 自身完成。该协议为 experimental，解析层全字段容错。

参考实现：ai-fuelgauge（协议交互）、codex-usage-monitor（查完即释放进程）。
"""

from __future__ import annotations

import dataclasses
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_TIMEOUT = 15.0  # 秒；超时即 kill 子进程（云端查询实测 2-8s，8s 太紧）
from . import __version__


CLIENT_INFO = {"name": "codex-quota", "version": __version__}


class AppServerError(Exception):
    """查询失败的统一错误类型，message 面向用户可读。"""


class CodexNotFoundError(AppServerError):
    pass


# 货币符号（余额型 provider 用）
CURRENCY_SYMBOLS = {"CNY": "¥", "USD": "$", "EUR": "€"}


@dataclass
class QuotaWindow:
    """单个限流窗口。字段缺失时全部为 None（未知），与 0（已用 0%）严格区分。

    两种形态：
    - 窗口型（codex/kimi）：used_percent + window_minutes + reset_at
    - 余额型（deepseek 等）：abs_remaining + abs_unit（无窗口、无重置时间）
    """

    used_percent: Optional[float] = None
    window_minutes: Optional[int] = None
    reset_at: Optional[float] = None  # Unix 秒
    abs_remaining: Optional[float] = None  # 余额型：绝对剩余量（如 12.34 元）
    abs_unit: Optional[str] = None         # 余额型单位：CNY / USD / credits …

    @property
    def is_balance(self) -> bool:
        """余额型：无百分比但有绝对余额。"""
        return self.used_percent is None and self.abs_remaining is not None

    @property
    def remaining_percent(self) -> Optional[float]:
        """剩余额度百分比（展示层统一用这个）。"""
        if self.used_percent is None:
            return None
        return max(0.0, 100.0 - self.used_percent)

    @property
    def abs_text(self) -> Optional[str]:
        """余额型展示文本：¥12.34 / $5.00 / 100 credits。"""
        if self.abs_remaining is None:
            return None
        unit = self.abs_unit or ""
        symbol = CURRENCY_SYMBOLS.get(unit)
        if symbol:
            return f"{symbol}{self.abs_remaining:.2f}"
        return f"{self.abs_remaining:.2f} {unit}".rstrip()

    @property
    def abs_level(self) -> Optional[str]:
        """余额告警等级：crit（红）/ warn（黄）/ ok（绿）。阈值按币种经验值。"""
        if self.abs_remaining is None:
            return None
        warn_at, crit_at = {"USD": (5.0, 1.0), "EUR": (5.0, 1.0)}.get(
            self.abs_unit or "", (20.0, 5.0))
        if self.abs_remaining <= crit_at:
            return "crit"
        if self.abs_remaining <= warn_at:
            return "warn"
        return "ok"

    @property
    def label(self) -> str:
        """窗口名称（随 i18n 语言输出）；余额型固定为"余额"。"""
        from .i18n import tr

        if self.is_balance:
            return tr("余额")
        m = self.window_minutes
        if m is None:
            return tr("窗口")
        if m <= 360:
            return tr("5小时")
        if m >= 5000:
            return tr("本周")
        return tr("{h}小时").format(h=f"{m / 60:.0f}")

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
    """一次查询的完整结果。fetched_at 为本地 Unix 秒。provider 标识数据源。"""

    fetched_at: float
    plan_type: Optional[str]
    limits: list[RateLimit]  # 主限额在前，附加限额桶（如 Spark）在后
    provider: str = "codex"

    @property
    def primary_limit(self) -> Optional[RateLimit]:
        return self.limits[0] if self.limits else None


def find_codex_bin() -> str:
    """定位 codex 可执行文件；CODEX_BIN 环境变量可覆盖。

    PATH 找不到时回退搜索：
    - Linux/mac: nvm 版本目录——nvm 切换默认 Node 版本后，装在旧版本
      全局下的 codex 会从 PATH 消失（实测踩坑：v20 装 codex，nvm 切到
      v24 后"未找到 codex"）
    - Windows: npm 全局目录的 codex.cmd（X_OK 无执行位语义，不查）
    """
    is_win = sys.platform == "win32"

    def _executable(path: str) -> bool:
        if not os.path.isfile(path):
            return False
        return True if is_win else os.access(path, os.X_OK)

    override = os.environ.get("CODEX_BIN")
    if override:
        if _executable(override):
            return override
        # 指向无效路径（填错/已卸载残留）不应硬失败——警告后继续正常发现流程，
        # 否则一个过期的环境变量会把整个定位链路锁死（内测实测踩到）
        import logging

        logging.getLogger("codex_quota.app_server").warning(
            "CODEX_BIN 指向的文件不可执行，忽略并继续自动发现: %s", override)
    tried = []
    path = shutil.which("codex")  # Windows 下 PATHEXT 已覆盖 codex.cmd
    if path is not None:
        return path
    tried.append("PATH")
    import glob

    if is_win:
        prefix = _npm_prefix()
        if prefix:
            tried.append(f"npm prefix: {prefix}")
        candidates = _windows_codex_candidates()
    else:
        candidates = sorted(
            glob.glob(os.path.join(os.path.expanduser("~"), ".nvm", "versions",
                                   "node", "*", "bin", "codex")),
            reverse=True,  # 版本号大的优先
        )
    for candidate in candidates:
        if _executable(candidate):
            return candidate
    tried.extend(candidates)
    # 报错信息直接带上所有找过的位置——远程排障不用来回要日志
    raise CodexNotFoundError(
        "未找到 codex 可执行文件。请先安装 Codex CLI 并登录（codex login），"
        "或设置 CODEX_BIN 环境变量。\n已查找: " + "；".join(tried)
    )


_NPM_PREFIX_UNSET = object()
_npm_prefix_cache: object = _NPM_PREFIX_UNSET


def _npm_prefix() -> Optional[str]:
    """npm 全局 prefix（可被 npm config 自定义，默认 %APPDATA%\\npm）。

    只在 PATH 找不到 codex 的兜底路径上调用一次并缓存；查询失败返回 None。
    """
    global _npm_prefix_cache
    if _npm_prefix_cache is not _NPM_PREFIX_UNSET:
        return _npm_prefix_cache or None  # type: ignore[return-value]
    prefix = ""
    npm = shutil.which("npm")
    if npm:
        try:
            from .proc import hidden_console_kwargs, run_external, wrap_cmd_shim

            out = run_external(wrap_cmd_shim([npm, "config", "get", "prefix"]),
                               capture_output=True, text=True, timeout=10,
                               **hidden_console_kwargs())
            if out.returncode == 0:
                prefix = (out.stdout or "").strip()
        except Exception:
            prefix = ""
    _npm_prefix_cache = prefix
    return prefix or None


def _windows_codex_candidates() -> list[str]:
    """Windows 下 codex 的常见安装位置（PATH 之外的兜底，按可能性排序）。"""
    appdata = os.environ.get("APPDATA") or ""
    local = os.environ.get("LOCALAPPDATA") or ""
    candidates = []
    prefix = _npm_prefix()  # npm 自定义 prefix（与默认不同才值得查）
    if prefix:
        candidates.append(os.path.join(prefix, "codex.cmd"))
    candidates += [
        os.path.join(appdata, "npm", "codex.cmd"),              # npm 全局默认
        # Codex 官方独立安装包（OpenAI\Codex 目录，bin 有无子级两种布局都试）
        os.path.join(local, "Programs", "OpenAI", "Codex", "bin", "codex.exe"),
        os.path.join(local, "Programs", "OpenAI", "Codex", "codex.exe"),
        os.path.join(local, "Programs", "codex", "codex.exe"),
        os.path.join(os.path.expanduser("~"), ".codex", "bin", "codex.exe"),
    ]
    return candidates


def codex_home() -> str:
    """Codex CLI 的数据目录（CODEX_HOME 可覆盖，默认 ~/.codex）。"""
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def is_logged_in() -> bool:
    """是否存在本地登录凭证（auth.json）。用于错误引导，绝不读取其内容。"""
    return os.path.isfile(os.path.join(codex_home(), "auth.json"))


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


def snapshot_to_dict(snap: QuotaSnapshot) -> dict[str, Any]:
    """序列化为可 JSON 化的 dict（dataclasses.asdict 的别名，集中在一处便于演进）。"""
    return dataclasses.asdict(snap)


def snapshot_from_dict(d: dict[str, Any]) -> QuotaSnapshot:
    """从 dict 还原快照。字段缺失/多余均容错（缓存跨版本兼容）。"""

    def _window(raw: Any) -> Optional[QuotaWindow]:
        if not isinstance(raw, dict):
            return None
        return QuotaWindow(
            used_percent=raw.get("used_percent"),
            window_minutes=raw.get("window_minutes"),
            reset_at=raw.get("reset_at"),
            abs_remaining=raw.get("abs_remaining"),
            abs_unit=raw.get("abs_unit"),
        )

    def _credits(raw: Any) -> Optional[Credits]:
        if not isinstance(raw, dict):
            return None
        return Credits(
            has_credits=bool(raw.get("has_credits")),
            unlimited=bool(raw.get("unlimited")),
            balance=raw.get("balance"),
        )

    limits: list[RateLimit] = []
    for raw in d.get("limits") or []:
        if not isinstance(raw, dict):
            continue
        limits.append(RateLimit(
            limit_id=raw.get("limit_id") or "?",
            limit_name=raw.get("limit_name"),
            plan_type=raw.get("plan_type"),
            primary=_window(raw.get("primary")) or QuotaWindow(),
            secondary=_window(raw.get("secondary")),
            credits=_credits(raw.get("credits")),
        ))
    return QuotaSnapshot(
        fetched_at=float(d.get("fetched_at") or 0),
        plan_type=d.get("plan_type"),
        limits=limits,
        provider=d.get("provider") or "codex",  # 旧缓存无此字段 → codex
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
        from .proc import hidden_console_kwargs, popen_external, wrap_cmd_shim

        # pythonw 启动的 GUI 没有可继承控制台；codex.cmd 会经 cmd.exe /c，
        # 不显式禁止窗口就可能在每次额度刷新时闪出黑框
        proc = popen_external(
            wrap_cmd_shim([self.codex_bin, "app-server"]),  # Windows npm 是 codex.cmd
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            **hidden_console_kwargs(),
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
        if sys.platform == "win32":
            # npm 的 codex.cmd 会形成 cmd.exe -> node/codex 进程树；只 terminate
            # 根 cmd 会留下 app-server。复用统一的 Windows taskkill /T 回收。
            from .proc import kill_tree

            kill_tree(proc, timeout=1)
            return
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)
