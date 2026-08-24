"""额度重置通知：检测窗口重置并通过 ntfy 推送到手机。

- ResetWatcher：盯住每个 (provider, 限额桶, 窗口) 的剩余量，
  从 < 99.5% 跳到 ≥ 99.5%（即重置回满）时产生一次事件；只在跳变时触发，不重复
- NtfyNotifier：POST https://ntfy.sh/<topic>，手机装 ntfy App 订阅同名主题即收。
  主题即凭证，自动生成并持久化在 settings.json
- NtfyCommandListener：反向通道。订阅 <主题>-cmd 的 JSON 流，手机向该主题发 "url"
  即触发回调（回推当前访问地址）——解决"想在手机上看时找不到网页地址"的问题
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
import urllib.request
from collections import deque
from typing import Callable, Optional

from .app_server import QuotaSnapshot
from .state import key_excluded

RESET_THRESHOLD = 99.5  # 剩余量跨过此线视为"重置回满"
DEFAULT_NTFY_SERVER = "https://ntfy.sh"


class ResetWatcher:
    """跨刷新记住上次剩余量，检测"回满"跳变。首次见到某窗口不产生事件。"""

    def __init__(self, threshold: float = RESET_THRESHOLD):
        self._threshold = threshold
        self._last: dict[tuple[str, str, str], float] = {}

    def check(self, provider: str, snap: QuotaSnapshot,
              excludes: frozenset[str] = frozenset()) -> list[str]:
        """返回本次新发生的重置事件描述列表（多数时候为空）。
        excludes 排除的 "provider:桶:窗口"（或桶级前缀）不产生事件，
        但状态照常跟踪——重新勾选后不会因"排除期间刚好回满"补发假事件。"""
        events: list[str] = []
        for limit in snap.limits:
            bucket = limit.limit_name or limit.limit_id
            for w in (limit.primary, limit.secondary):
                if w is None or w.remaining_percent is None:
                    continue
                key = (provider, bucket, w.label)
                prev = self._last.get(key)
                cur = w.remaining_percent
                excluded = key_excluded(f"{provider}:{bucket}:{w.label}", excludes)
                if (prev is not None and prev < self._threshold <= cur
                        and not excluded):
                    # 事件不含 provider（调用方知道是谁）；主限额不带桶名，
                    # 与托盘摘要/勾选标签的展示习惯一致
                    event = w.label if limit is snap.primary_limit \
                        else f"{bucket} · {w.label}"
                    events.append(event)
                self._last[key] = cur
        return events


class NtfyNotifier:
    """ntfy 发布端。推送失败静默返回 False（通知不是关键路径）。"""

    def __init__(self, server: str = DEFAULT_NTFY_SERVER, topic: str = "",
                 timeout: float = 8.0):
        self.server = server.rstrip("/")
        self.topic = topic
        self.timeout = timeout

    @property
    def subscribe_url(self) -> str:
        return f"{self.server}/{self.topic}"

    def publish(self, title: str, body: str, *, priority: str = "urgent",
                tags: str = "white_check_mark", click: str = "") -> bool:
        """发推送。priority 默认 urgent(5)：额度重置/地址变更都是用户要立刻知道的事，
        且 ntfy App 可按优先级过滤提醒（用户设最高提醒时只有 urgent 会响）。
        click 非空时带 Click header：手机上点通知直接用浏览器打开该 URL。"""
        if not self.topic:
            return False
        headers = {
            # ntfy 的 Title header 只支持 ASCII，标题固定英文，中文放正文
            "Title": title,
            "Priority": priority,
            "Tags": tags,
        }
        if click:
            headers["Click"] = click
        req = urllib.request.Request(
            f"{self.server}/{self.topic}",
            data=body.encode("utf-8"),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status == 200
        except Exception:
            return False


def _ascii_title(display_name: str) -> str:
    """ntfy 的 Title 头只支持 ASCII——把 provider 显示名放进标题，
    锁屏上一眼看出是谁的额度回满（之前标题固定 codex-quota，分不清）。"""
    name = display_name.encode("ascii", "ignore").decode().strip()
    return f"{name} quota reset" if name else "codex-quota"


def _actual_reset_text(snap: QuotaSnapshot, event: str,
                       now: Optional[float] = None) -> str:
    """从快照反推该事件的实际重置时刻（reset_at 指向下次重置，减去窗口长度）。

    检测可能迟到（断网/机器休眠期间重置已经发生，恢复后第一次查询才发现），
    只写"已重置回 100%"会让用户误以为刚刚才重置。匹配不上或时间不合理时
    返回空串，不影响正文。
    """
    now = now if now is not None else time.time()
    for limit in snap.limits:
        bucket = limit.limit_name or limit.limit_id
        for w in (limit.primary, limit.secondary):
            if w is None or w.reset_at is None or not w.window_minutes:
                continue
            if event != w.label and event != f"{bucket} · {w.label}":
                continue
            actual = w.reset_at - w.window_minutes * 60
            delay = now - actual
            if not (0 <= delay < w.window_minutes * 60):
                return ""  # 推算落在窗口外，数据不可信，宁可不写
            fmt = "%H:%M" if delay < 20 * 3600 else "%m-%d %H:%M"
            return dt.datetime.fromtimestamp(actual).strftime(fmt)
    return ""


def notify_resets(notifier: Optional[NtfyNotifier], watcher: ResetWatcher,
                  provider: str, display_name: str, snap: QuotaSnapshot,
                  excludes: frozenset[str] = frozenset()) -> list[str]:
    """检测 + 推送；返回触发的事件列表（供日志/测试）。excludes 同 check。"""
    events = watcher.check(provider, snap, excludes)
    if notifier is not None:
        for event in events:
            when = _actual_reset_text(snap, event)
            detail = f"{event} · 重置于 {when}" if when else event
            notifier.publish(
                _ascii_title(display_name),
                f"✅ {display_name} 额度已重置回 100%（{detail}）",
            )
    return events


class NtfyCommandListener:
    """后台线程订阅 ntfy 命令主题的 JSON 流，每条消息回调给命令处理器。

    since=启动时刻的时间戳：只响应启动后新发的命令（ntfy 不接受 since=now，
    只认时间戳/时长/all）；重连用最近一条消息的时间戳，断线窗口内的命令不丢，
    重投递的消息按 id 去重。断线按指数退避自动重连（最长 60s）。
    触发即凭证同主题：知道命令主题名也只能让电脑把地址推给主主题订阅者，
    攻击者得不到任何信息。
    """

    def __init__(self, server: str, topic: str, on_command: Callable[[str], None], *,
                 timeout: float = 120.0):
        self.server = server.rstrip("/")
        self.topic = topic
        self._on_command = on_command
        self._timeout = timeout
        self._since = int(time.time())      # 只收启动之后的消息
        self._seen_ids: deque = deque(maxlen=200)  # 重连重投递去重
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._resp = None  # 当前流式响应，stop() 时关闭以打断阻塞读取

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="ntfy-cmd-listener")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)

    # ---------- 内部 ----------

    def _run(self) -> None:
        log = logging.getLogger("codex_quota.notify")
        backoff = 2.0
        failed = False  # 首次断开记 WARNING，连续失败降级 DEBUG 防刷屏
        while not self._stop.is_set():
            try:
                req = urllib.request.Request(
                    f"{self.server}/{self.topic}/json?since={self._since}")
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    self._resp = resp
                    backoff = 2.0  # 连上即重置退避
                    failed = False
                    for raw in resp:  # NDJSON 流，一行一个事件
                        if self._stop.is_set():
                            return
                        self._handle(raw)
            except Exception as exc:
                if self._stop.is_set():
                    return
                (log.debug if failed else log.warning)(
                    "ntfy 命令流断开（%s），将自动重连", exc)
                failed = True
            finally:
                self._resp = None
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    def _handle(self, raw: bytes) -> None:
        try:
            evt = json.loads(raw)
        except ValueError:
            return
        if evt.get("event") != "message":
            return  # keepalive/open 等事件忽略
        mid = evt.get("id")
        if mid:
            if mid in self._seen_ids:
                return  # 重连后的重投递
            self._seen_ids.append(mid)
        ts = evt.get("time")
        if isinstance(ts, int) and ts > self._since:
            self._since = ts  # 下次重连从这里续，不丢断线窗口内的命令
        msg = (evt.get("message") or "").strip()
        if msg:
            try:
                self._on_command(msg)
            except Exception:
                pass  # 回调失败不拖垮监听线程
