"""Kimi 用量 provider：`kimi web` 本地服务器 + /api/v1/oauth/usage。

交互方式（2026-08-12 实测，kimi-code 0.35.0）：
- spawn `kimi web --port <随机空闲端口>`，从 stdout 解析 "Token: xxx"
  （token 与本地凭证绑定，多次启动相同；不自动打开浏览器）
- GET /api/v1/oauth/usage（Authorization: Bearer <token>）→ 用量 JSON
- GET /api/v1/auth → default_model（展示用，失败可容忍）
- 服务器启动约 3–5s，保活整个应用周期；close() 杀整个进程组
  （服务器进程名为 kimi-code，可能是子进程，必须 killpg）

响应映射：summary(周窗)→primary，limits[0](5h)→secondary；
used/limit*100 → used_percent；ISO8601 reset_at → Unix 秒。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Optional

from ..app_server import QuotaSnapshot, QuotaWindow, RateLimit

logger = logging.getLogger("codex_quota.kimi")

TOKEN_RE = re.compile(r"Token:\s+(\S+)")
UNIT_MINUTES = {"minute": 1, "hour": 60, "day": 1440, "week": 10080}


class KimiError(Exception):
    pass


def find_kimi_bin() -> Optional[str]:
    """定位 kimi 可执行文件；KIMI_BIN 环境变量优先。找不到返回 None（provider 不启用）。"""
    override = os.environ.get("KIMI_BIN")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    found = shutil.which("kimi")
    if found:
        return found
    candidate = os.path.expanduser("~/.kimi-code/bin/kimi")
    return candidate if os.path.isfile(candidate) else None


def _parse_iso8601(ts: Any) -> Optional[float]:
    if not isinstance(ts, str):
        return None
    try:
        # Python 3.10 的 fromisoformat 不认 "Z"，手动替换
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _window_from_entry(entry: Any) -> Optional[QuotaWindow]:
    if not isinstance(entry, dict):
        return None
    w = entry.get("window") or {}
    duration = w.get("duration")
    mult = UNIT_MINUTES.get(w.get("unit"))
    minutes = int(duration * mult) if isinstance(duration, (int, float)) and mult else None

    used, limit = entry.get("used"), entry.get("limit")
    pct = None
    if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        pct = used / limit * 100
    return QuotaWindow(
        used_percent=pct,
        window_minutes=minutes,
        reset_at=_parse_iso8601(entry.get("reset_at")),
    )


def parse_kimi_usage(payload: dict[str, Any], model: Optional[str] = None,
                     now: Optional[float] = None) -> QuotaSnapshot:
    """把 /api/v1/oauth/usage 的响应映射为 QuotaSnapshot。"""
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("kind") != "ok":
        raise KimiError(f"kimi 用量接口返回异常: kind={data.get('kind') if isinstance(data, dict) else data!r}")

    primary = _window_from_entry(data.get("summary")) or QuotaWindow()
    limits = data.get("limits") or []
    secondary = _window_from_entry(limits[0]) if limits else None

    rl = RateLimit(limit_id="kimi", plan_type=model, primary=primary, secondary=secondary)
    return QuotaSnapshot(
        fetched_at=now if now is not None else time.time(),
        plan_type=model,
        limits=[rl],
        provider="kimi",
    )


class KimiProvider:
    name = "kimi"
    display_name = "Kimi"

    def __init__(self, kimi_bin: Optional[str] = None, *,
                 base_url: Optional[str] = None, token: Optional[str] = None,
                 startup_timeout: float = 20.0, request_timeout: float = 8.0,
                 restart_cooldown: float = 300.0):
        self._bin = kimi_bin
        self._base_url = base_url      # 注入则跳过进程管理（测试/外部服务器）
        self._token = token
        self._startup_timeout = startup_timeout
        self._request_timeout = request_timeout
        self._restart_cooldown = restart_cooldown
        self._last_restart = 0.0
        self._proc: Optional[subprocess.Popen] = None

    # ---------- 进程管理 ----------

    def _spawn_args(self, port: int) -> list[str]:
        # --no-open：禁止服务器启动时自动打开浏览器标签页
        return [self._bin, "web", "--port", str(port), "--no-open"]

    def _ensure_server(self) -> None:
        if self._base_url and self._token:
            return  # 注入模式
        if self._proc is not None and self._proc.poll() is None and self._token:
            return  # 已保活

        if self._bin is None:
            self._bin = find_kimi_bin()
        if self._bin is None:
            raise KimiError("未找到 kimi 可执行文件（可设置 KIMI_BIN）")

        # 随机空闲端口
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        self._proc = subprocess.Popen(
            self._spawn_args(port),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,  # 独立进程组，close 时 killpg 一锅端
        )
        lines: queue.Queue[str] = queue.Queue()

        def _pump() -> None:
            assert self._proc is not None and self._proc.stdout is not None
            for line in self._proc.stdout:
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
            m = TOKEN_RE.search(line)
            if m:
                self._token = m.group(1)
                self._base_url = f"http://127.0.0.1:{port}"
                logger.info("kimi web 已启动（端口 %s）", port)
                return
        self.close()
        raise KimiError("kimi web 启动超时或未输出 Token")

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    # ---------- 查询 ----------

    def _get_json(self, path: str) -> dict[str, Any]:
        assert self._base_url and self._token
        req = urllib.request.Request(
            self._base_url + path,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        with urllib.request.urlopen(req, timeout=self._request_timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _restart_server(self) -> None:
        """重启保活服务器。带冷却：频繁重启会反复拉进程，必须限流。"""
        now = time.monotonic()
        if now - self._last_restart < self._restart_cooldown:
            logger.info("kimi web 重启冷却中，跳过本次重启")
            raise KimiError("kimi web 无响应（重启冷却中，稍后自动恢复）")
        self._last_restart = now
        logger.warning("kimi web 连接失败，重启服务器")
        self.close()
        self._base_url = self._token = None
        self._ensure_server()

    def fetch(self) -> QuotaSnapshot:
        self._ensure_server()
        try:
            usage = self._get_json("/api/v1/oauth/usage")
        except urllib.error.HTTPError as exc:
            # HTTP 层错误（401/404…）：连接是通的，重启无意义
            raise KimiError(f"kimi 接口返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError):
            # 连接级失败：服务器可能已死，重启一次再试（冷却限流）
            self._restart_server()
            try:
                usage = self._get_json("/api/v1/oauth/usage")
            except Exception as exc:
                raise KimiError("kimi web 重启后仍无响应") from exc

        model = None
        try:
            auth = self._get_json("/api/v1/auth")
            model = (auth.get("data") or {}).get("default_model")
        except Exception:
            pass  # 模型名只是展示，拿不到不致命
        return parse_kimi_usage(usage, model=model)
