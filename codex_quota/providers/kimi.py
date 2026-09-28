"""Kimi 用量 provider：`kimi web` 本地服务器 + /api/v1/oauth/usage。

交互方式（2026-09-01）：
- 不启动、不停止、不重启 Kimi Web；服务生命周期由 stupid 项目的
  `kimi-code-web.service` 唯一负责。
- 扫描 `~/.kimi-code/server/instances/*.json`，优先复用固定端口 58627
  且心跳新鲜的实例。
- 每次请求读取 `~/.kimi-code/server.token`；HTTP 401 时重新读取并重试一次。
- GET /api/v1/oauth/usage（Authorization: Bearer <token>）→ 用量 JSON
- GET /api/v1/auth → default_model（展示用，失败可容忍）

响应映射：summary(周窗)→primary，limits[0](5h)→secondary；
used/limit*100 → used_percent；ISO8601 reset_at → Unix 秒。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Optional

from ..app_server import QuotaSnapshot, QuotaWindow, RateLimit

logger = logging.getLogger("codex_quota.kimi")

UNIT_MINUTES = {"minute": 1, "hour": 60, "day": 1440, "week": 10080}
INSTANCE_MAX_AGE_MS = 120_000
DEFAULT_OWNER_PORT = 58627


class KimiError(Exception):
    pass


def find_kimi_bin() -> Optional[str]:
    """定位 kimi 可执行文件；KIMI_BIN 环境变量优先。找不到返回 None（provider 不启用）。
    Windows：X_OK 无执行位语义（退化为"存在即可"），候选带 .exe/.cmd 后缀。
    """
    import sys

    is_win = sys.platform == "win32"

    def _usable(path: str) -> bool:
        if not os.path.isfile(path):
            return False
        return True if is_win else os.access(path, os.X_OK)

    override = os.environ.get("KIMI_BIN")
    if override and _usable(override):
        return override
    found = shutil.which("kimi")  # PATHEXT 已覆盖 kimi.cmd / kimi.exe
    if found:
        return found
    names = ["kimi.exe", "kimi.cmd"] if is_win else ["kimi"]
    for name in names:
        candidate = os.path.expanduser(f"~/.kimi-code/bin/{name}")
        if os.path.isfile(candidate):
            return candidate
    return None


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
                 restart_cooldown: float = 300.0,
                 instance_dir: Optional[str] = None,
                 token_path: Optional[str] = None,
                 preferred_port: Optional[int] = None):
        # kimi_bin/startup_timeout/restart_cooldown 仅为旧调用兼容，不再用于拉起进程。
        self._bin = kimi_bin
        self._base_url = base_url
        self._token = token
        self._request_timeout = request_timeout
        root = os.path.expanduser("~/.kimi-code")
        self._instance_dir = instance_dir or os.path.join(root, "server", "instances")
        self._token_path = token_path or os.path.join(root, "server.token")
        self._preferred_port = preferred_port or int(os.environ.get("KIMI_WEB_PORT", DEFAULT_OWNER_PORT))
        self._injected = base_url is not None and token is not None

    # ---------- 只读实例发现 ----------

    def _read_token(self) -> str:
        try:
            with open(self._token_path, "r", encoding="utf-8") as f:
                token = f.read().strip()
        except OSError as exc:
            raise KimiError("未找到 Kimi Web token 文件；不会自动 login 或启动服务") from exc
        if not token:
            raise KimiError("Kimi Web token 文件为空")
        return token

    def _discover_server(self, *, force: bool = False) -> None:
        if self._injected and not force:
            return
        now_ms = time.time() * 1000
        candidates = []
        try:
            names = os.listdir(self._instance_dir)
        except OSError as exc:
            raise KimiError("未发现 Kimi Web 实例；请检查 stupid 的 kimi-code-web.service") from exc
        for name in names:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self._instance_dir, name), "r", encoding="utf-8") as f:
                    item = json.load(f)
                if (item.get("port") and item.get("pid")
                        and now_ms - float(item.get("heartbeat_at", 0)) <= INSTANCE_MAX_AGE_MS):
                    candidates.append(item)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        if not candidates:
            raise KimiError("没有心跳新鲜的 Kimi Web 实例；不会自动启动新实例")
        candidates.sort(key=lambda x: float(x.get("heartbeat_at", 0)), reverse=True)
        selected = next((x for x in candidates if int(x["port"]) == self._preferred_port), candidates[0])
        host = selected.get("host") or "127.0.0.1"
        self._base_url = f"http://{host}:{int(selected['port'])}"
        self._token = self._read_token()
        logger.info("复用 Kimi Web（端口 %s，PID %s）", selected["port"], selected["pid"])

    def _ensure_server(self) -> None:
        if self._base_url and self._token:
            return
        self._discover_server()

    def close(self) -> None:
        """客户端不拥有 Kimi Web，退出时绝不停止共享服务。"""
        return None

    # ---------- 查询 ----------

    def _get_json(self, path: str) -> dict[str, Any]:
        assert self._base_url and self._token
        req = urllib.request.Request(
            self._base_url + path,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        with urllib.request.urlopen(req, timeout=self._request_timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def fetch(self) -> QuotaSnapshot:
        self._ensure_server()
        try:
            usage = self._get_json("/api/v1/oauth/usage")
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and not self._injected:
                self._token = self._read_token()
                try:
                    usage = self._get_json("/api/v1/oauth/usage")
                except urllib.error.HTTPError as retry_exc:
                    raise KimiError(f"kimi 接口返回 HTTP {retry_exc.code}") from retry_exc
            else:
                raise KimiError(f"kimi 接口返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError):
            # 仅重新发现 owner 实例；绝不自行重启或 login。
            if self._injected:
                raise KimiError("kimi web 无响应")
            self._base_url = self._token = None
            self._discover_server(force=True)
            try:
                usage = self._get_json("/api/v1/oauth/usage")
            except Exception as exc:
                raise KimiError("重新发现 Kimi Web 后仍无响应") from exc

        model = None
        try:
            auth = self._get_json("/api/v1/auth")
            model = (auth.get("data") or {}).get("default_model")
        except Exception:
            pass  # 模型名只是展示，拿不到不致命
        return parse_kimi_usage(usage, model=model)
