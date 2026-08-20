"""重置检测与 ntfy 推送测试。"""

from __future__ import annotations

import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from codex_quota.app_server import QuotaWindow
from codex_quota.notify import (
    NtfyCommandListener,
    NtfyNotifier,
    ResetWatcher,
    notify_resets,
)
from tests.conftest import codex_snapshot


def snap_with(weekly_used, hourly_used=None):
    """构造只含指定已用量的快照（weekly 主窗 + 可选 5h 副窗）。"""
    snap = codex_snapshot()
    main = snap.primary_limit
    main.primary.used_percent = weekly_used
    if hourly_used is None:
        main.secondary = None
    else:
        main.secondary = QuotaWindow(used_percent=hourly_used, window_minutes=300)
    snap.limits = [main]  # 去掉 Spark 桶，专注主限额
    return snap


class TestResetWatcher:
    def test_first_seen_no_event(self):
        w = ResetWatcher()
        assert w.check("codex", snap_with(weekly_used=91)) == []

    def test_reset_transition_fires_once(self):
        w = ResetWatcher()
        w.check("codex", snap_with(weekly_used=91))
        events = w.check("codex", snap_with(weekly_used=0))   # 重置回满
        assert len(events) == 1
        assert "codex" in events[0] and "本周" in events[0]
        assert w.check("codex", snap_with(weekly_used=0)) == []  # 不重复

    def test_normal_decline_no_event(self):
        w = ResetWatcher()
        w.check("codex", snap_with(weekly_used=50))
        assert w.check("codex", snap_with(weekly_used=60)) == []

    def test_partial_recovery_no_event(self):
        w = ResetWatcher()
        w.check("codex", snap_with(weekly_used=91))
        # 99.5 阈值：used 0.4% → remaining 99.6 触发；used 1% → 99 不触发
        assert w.check("codex", snap_with(weekly_used=1)) == []

    def test_multi_window_and_provider_isolated(self):
        w = ResetWatcher()
        w.check("codex", snap_with(weekly_used=91, hourly_used=80))
        kimi = snap_with(weekly_used=50)
        kimi.provider = "kimi"
        w.check("kimi", kimi)
        # 只有 codex 的 5小时窗口回满
        events = w.check("codex", snap_with(weekly_used=92, hourly_used=0))
        assert len(events) == 1
        assert "5小时" in events[0]

    def test_none_remaining_ignored(self):
        w = ResetWatcher()
        snap = snap_with(weekly_used=91)
        snap.primary_limit.primary.used_percent = None
        assert w.check("codex", snap) == []


class _Capture:
    requests: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            _Capture.requests.append({
                "path": self.path,
                "title": self.headers.get("Title"),
                "priority": self.headers.get("Priority"),
                "click": self.headers.get("Click"),
                "body": body.decode("utf-8"),
            })
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass


class TestNtfyNotifier:
    @pytest.fixture
    def server(self):
        _Capture.requests = []
        srv = HTTPServer(("127.0.0.1", 0), _Capture.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{srv.server_port}"
        srv.shutdown()

    def test_publish_format(self, server):
        n = NtfyNotifier(server=server, topic="my-topic")
        assert n.publish("codex-quota", "✅ 额度已重置回 100%：Codex") is True
        req = _Capture.requests[0]
        assert req["path"] == "/my-topic"
        assert req["title"] == "codex-quota"   # Title 保持 ASCII
        assert req["priority"] == "urgent"
        assert "重置回 100%" in req["body"]    # 中文在正文

    def test_empty_topic_noop(self, server):
        assert NtfyNotifier(server=server, topic="").publish("t", "b") is False
        assert _Capture.requests == []

    def test_click_header(self, server):
        """click 参数 → Click header（手机点通知直接打开 URL）；不传则不带该 header。"""
        n = NtfyNotifier(server=server, topic="t")
        url = "https://abc.trycloudflare.com/t/xyz/"
        assert n.publish("codex-quota", "地址", click=url) is True
        assert _Capture.requests[0]["click"] == url
        assert n.publish("codex-quota", "重置") is True
        assert _Capture.requests[1]["click"] is None

    def test_server_down_returns_false(self):
        n = NtfyNotifier(server="http://127.0.0.1:1", topic="t", timeout=1)
        assert n.publish("t", "b") is False

    def test_subscribe_url(self):
        n = NtfyNotifier(server="https://ntfy.sh/", topic="abc")
        assert n.subscribe_url == "https://ntfy.sh/abc"


class TestNotifyResets:
    def test_end_to_end(self):
        sent = []

        class FakeNotifier:
            def publish(self, title, body, **kw):
                sent.append(body)
                return True

        watcher = ResetWatcher()
        assert notify_resets(FakeNotifier(), watcher, "codex", "Codex",
                             snap_with(91)) == []
        events = notify_resets(FakeNotifier(), watcher, "codex", "Codex",
                               snap_with(0))
        assert len(events) == 1
        assert len(sent) == 1 and "Codex" in sent[0]

    def test_no_notifier_still_detects(self):
        watcher = ResetWatcher()
        notify_resets(None, watcher, "codex", "Codex", snap_with(91))
        events = notify_resets(None, watcher, "codex", "Codex", snap_with(0))
        assert len(events) == 1


def _msg(text):
    return json.dumps({"event": "message", "message": text}).encode()


class TestNtfyCommandListener:
    """_handle 纯逻辑 + 真实 NDJSON 流的端到端。"""

    def _listener(self, hits):
        return NtfyCommandListener("http://unused", "t", lambda: hits.append(1))

    def test_trigger_words(self):
        hits = []
        li = self._listener(hits)
        li._handle(_msg("url"))
        li._handle(_msg("URL"))          # 大小写不敏感
        li._handle(_msg("  地址  "))      # 首尾空白容忍
        li._handle(_msg("发个url给我"))   # 包含触发词即可
        assert len(hits) == 4

    def test_non_trigger_ignored(self):
        hits = []
        li = self._listener(hits)
        li._handle(_msg("hello"))
        li._handle(json.dumps({"event": "keepalive"}).encode())  # 非 message 事件
        li._handle(b"not json")                                   # 坏行
        li._handle(json.dumps({"event": "message"}).encode())     # 无 message 字段
        assert hits == []

    def test_callback_exception_swallowed(self):
        def boom():
            raise RuntimeError("x")

        li = NtfyCommandListener("http://unused", "t", boom)
        li._handle(_msg("url"))  # 不抛出

    def test_stream_end_to_end(self):
        """本地 NDJSON 流：写入触发消息后，监听线程应回调。"""
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                self.wfile.write(_msg("url") + b"\n")
                self.wfile.flush()
                self.close_connection = True  # 写一条就断，模拟流结束

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            hits = []
            li = NtfyCommandListener(f"http://127.0.0.1:{srv.server_port}", "t",
                                     lambda: hits.append(1), timeout=2)
            li.start()
            for _ in range(50):  # 最多等 5s
                if hits:
                    break
                threading.Event().wait(0.1)
            li.stop()
            assert hits, "监听线程未在超时内触发回调"
        finally:
            srv.shutdown()
