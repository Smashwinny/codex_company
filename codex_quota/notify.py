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
import os
import threading
import time
import urllib.request
import uuid
from collections import deque
from typing import Callable, Optional

from .app_server import QuotaSnapshot
from .net import https_context
from .state import key_excluded
from .sysdirs import cache_dir

RESET_THRESHOLD = 99.5  # 剩余量跨过此线视为"重置回满"
DEFAULT_NTFY_SERVER = "https://ntfy.sh"
OUTBOX_NAME = "notify-outbox.json"
RETRY_DELAYS_S = (5, 15, 30, 60, 120, 300)


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
    """ntfy 发布端；重要通知可先持久入队后异步重试。"""

    def __init__(self, server: str = DEFAULT_NTFY_SERVER, topic: str = "",
                 timeout: float = 8.0, *, outbox_path: Optional[str] = None,
                 retry_delays: tuple[float, ...] = RETRY_DELAYS_S):
        self.server = server.rstrip("/")
        self.topic = topic
        self.timeout = timeout
        self._outbox_path = outbox_path or os.path.join(cache_dir(), OUTBOX_NAME)
        self._retry_delays = retry_delays or RETRY_DELAYS_S
        self._outbox_lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        if self._load_outbox():
            self._ensure_worker()  # 上次退出/休眠前未发出的事件继续补发

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
            with urllib.request.urlopen(req, timeout=self.timeout, context=https_context()) as resp:
                return resp.status == 200
        except Exception as exc:
            logging.getLogger("codex_quota.notify").warning(
                "ntfy 发送失败: %s", exc)
            return False

    def enqueue(self, key: str, title: str, body: str, *,
                priority: str = "urgent", tags: str = "white_check_mark",
                click: str = "", detected_at: Optional[float] = None) -> bool:
        """持久化后立即返回；后台发送失败会重试，不阻塞 Qt 主线程。"""
        if not self.topic:
            return False
        detected = detected_at if detected_at is not None else time.time()
        with self._outbox_lock:
            items = self._load_outbox()
            if not any(item.get("key") == key for item in items):
                items.append({
                    "id": uuid.uuid4().hex,
                    "key": key,
                    "title": title,
                    "body": body,
                    "priority": priority,
                    "tags": tags,
                    "click": click,
                    "detected_at": detected,
                    "attempt": 0,
                    "next_try": 0.0,
                })
                if not self._save_outbox(items):
                    return False
                logging.getLogger("codex_quota.notify").info(
                    "重置通知已入队 key=%s detected_at=%s", key,
                    dt.datetime.fromtimestamp(detected).isoformat(timespec="seconds"))
        self._ensure_worker()
        self._wake.set()
        return True

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._worker is not None:
            self._worker.join(timeout=max(3.0, self.timeout + 1.0))

    def _load_outbox(self) -> list[dict]:
        try:
            with open(self._outbox_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return raw if isinstance(raw, list) else []
        except (OSError, ValueError):
            return []

    def _save_outbox(self, items: list[dict]) -> bool:
        try:
            parent = os.path.dirname(self._outbox_path)
            os.makedirs(parent, exist_ok=True)
            tmp = self._outbox_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._outbox_path)
            return True
        except OSError as exc:
            logging.getLogger("codex_quota.notify").warning(
                "通知队列持久化失败: %s", exc)
            return False

    def _ensure_worker(self) -> None:
        with self._outbox_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._run_outbox, daemon=True, name="ntfy-outbox")
            self._worker.start()

    def _run_outbox(self) -> None:
        log = logging.getLogger("codex_quota.notify")
        while not self._stop.is_set():
            with self._outbox_lock:
                items = self._load_outbox()
                now = time.time()
                due = next((dict(item) for item in items
                            if float(item.get("next_try", 0)) <= now), None)
                next_due = min((float(item.get("next_try", 0)) for item in items),
                               default=now + 300)
            if due is None:
                self._wake.wait(max(0.1, min(300.0, next_due - time.time())))
                self._wake.clear()
                continue

            ok = self.publish(
                str(due.get("title", "codex-quota")), str(due.get("body", "")),
                priority=str(due.get("priority", "urgent")),
                tags=str(due.get("tags", "white_check_mark")),
                click=str(due.get("click", "")))
            with self._outbox_lock:
                items = self._load_outbox()
                current = next((item for item in items
                                if item.get("id") == due.get("id")), None)
                if current is None:
                    continue
                if ok:
                    items.remove(current)
                    self._save_outbox(items)
                    delay = max(0.0, time.time() - float(current.get("detected_at", time.time())))
                    log.info("重置通知已送达 ntfy key=%s delay=%.1fs attempts=%s",
                             current.get("key"), delay,
                             int(current.get("attempt", 0)) + 1)
                else:
                    attempt = int(current.get("attempt", 0)) + 1
                    current["attempt"] = attempt
                    delay = self._retry_delays[min(attempt - 1,
                                                   len(self._retry_delays) - 1)]
                    current["next_try"] = time.time() + delay
                    self._save_outbox(items)
                    log.warning("重置通知尚未送达 key=%s attempt=%d retry_in=%.0fs",
                                current.get("key"), attempt, delay)


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
            title = _ascii_title(display_name)
            body = f"✅ {display_name} 额度已重置回 100%（{detail}）"
            actual = _actual_reset_epoch(snap, event)
            key = f"{provider}:{event}:{int(actual or snap.fetched_at)}"
            enqueue = getattr(notifier, "enqueue", None)
            if callable(enqueue):
                if not enqueue(key, title, body, detected_at=time.time()):
                    # 磁盘只读/满等导致队列无法落盘时，仍尝试直接发送。
                    notifier.publish(title, body)
            else:  # 保持第三方/测试 notifier 的简单 publish 契约
                notifier.publish(title, body)
    return events


def _actual_reset_epoch(snap: QuotaSnapshot, event: str) -> Optional[float]:
    """返回可信的实际重置时刻，用于持久队列去重。"""
    now = time.time()
    for limit in snap.limits:
        bucket = limit.limit_name or limit.limit_id
        for w in (limit.primary, limit.secondary):
            if w is None or w.reset_at is None or not w.window_minutes:
                continue
            if event not in (w.label, f"{bucket} · {w.label}"):
                continue
            actual = w.reset_at - w.window_minutes * 60
            if 0 <= now - actual < w.window_minutes * 60:
                return actual
    return None


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
                with urllib.request.urlopen(req, timeout=self._timeout, context=https_context()) as resp:
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
