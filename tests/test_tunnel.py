"""cloudflared 隧道模块测试（用假脚本，不依赖真实 cloudflared/网络）。"""

from __future__ import annotations

import io
import os
import sys

import pytest

from codex_quota.__main__ import (
    _quiesce_tunnel_restart,
    _start_tunnel_guarded,
)
from codex_quota.tunnel import Tunnel, TunnelError, find_cloudflared


@pytest.fixture
def fake_cloudflared(tmp_path):
    """假 cloudflared：stderr 输出一段模拟日志（含公网地址）后睡眠。"""
    script = tmp_path / "cloudflared"
    script.write_text(
        "#!/bin/sh\n"
        'echo "INF Starting tunnel" >&2\n'
        'echo "INF +-------------------------------------------------------------+" >&2\n'
        'echo "INF |  https://abc-def-123.trycloudflare.com                      |" >&2\n'
        'echo "INF +-------------------------------------------------------------+" >&2\n'
        "sleep 300\n"
    )
    script.chmod(0o755)
    return str(script)


class TestFindBinary:
    def test_vendor_fallback(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        # 本机 vendor/bin/cloudflared 由 install.sh 下载，可能存在也可能不存在；
        # 只验证函数不崩且返回 None 或存在的路径
        result = find_cloudflared()
        assert result is None or os.path.isfile(result)


class TestRestartShutdownGuard:
    def test_stopping_prevents_spawn(self):
        import threading

        class FakeTunnel:
            def start(self):
                raise AssertionError("退出置位后不得启动 cloudflared")

        stopping = threading.Event()
        stopping.set()
        assert _start_tunnel_guarded(
            FakeTunnel(), stopping, threading.Lock()) is None

    def test_shutdown_waits_for_inflight_start_before_setting_stop(self):
        import threading

        entered = threading.Event()
        release = threading.Event()
        stopping = threading.Event()
        gate = threading.Lock()

        class FakeTunnel:
            def start(self):
                entered.set()
                assert release.wait(2)
                return "https://example.trycloudflare.com"

        result = []
        worker = threading.Thread(target=lambda: result.append(
            _start_tunnel_guarded(FakeTunnel(), stopping, gate)))
        worker.start()
        assert entered.wait(1)

        shutdown = threading.Thread(target=lambda: _quiesce_tunnel_restart(
            None, stopping, gate, worker))
        shutdown.start()
        assert not stopping.wait(0.05)  # start() 在途时退出置位须等待同一门锁
        release.set()
        worker.join(2)
        shutdown.join(2)

        assert result == ["https://example.trycloudflare.com"]
        assert stopping.is_set()
        assert not worker.is_alive()


class TestTunnelTextDecoding:
    def test_bad_utf8_stderr_is_replaced_and_url_still_parsed(self, monkeypatch):
        raw = (b"INF cloudflared bad byte: \x91\n"
               b"INF | https://utf8-safe.trycloudflare.com |\n")

        class FakeProcess:
            pid = 4242
            stderr = io.TextIOWrapper(io.BytesIO(raw), encoding="gbk")

            @staticmethod
            def poll():
                return None

        fake = FakeProcess()
        monkeypatch.setattr("codex_quota.tunnel.proc.spawn_detached",
                            lambda *_args, **_kwargs: fake)
        monkeypatch.setattr("codex_quota.tunnel.proc.record_child",
                            lambda *_args: None)
        monkeypatch.setattr("codex_quota.tunnel.proc.kill_tree",
                            lambda *_args, **_kwargs: None)

        tunnel = Tunnel(local_port=8642, cloudflared_bin="cloudflared",
                        startup_timeout=2)
        assert tunnel.start() == "https://utf8-safe.trycloudflare.com"
        assert fake.stderr.encoding.lower().replace("-", "") == "utf8"
        assert fake.stderr.errors == "replace"
        tunnel.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 进程组/执行位语义，Windows 分支由 test_proc 等注入式测试覆盖")
class TestTunnelLifecycle:
    def test_url_parsed_from_stderr(self, fake_cloudflared):
        t = Tunnel(local_port=8642, cloudflared_bin=fake_cloudflared, startup_timeout=10)
        url = t.start()
        assert url == "https://abc-def-123.trycloudflare.com"
        assert t.public_url == url
        proc = t._proc
        assert proc.poll() is None
        t.stop()
        assert proc.poll() is not None      # 进程已被杀
        assert t.public_url is None          # 地址失效

    def test_start_idempotent_restart(self, fake_cloudflared):
        t = Tunnel(local_port=8642, cloudflared_bin=fake_cloudflared, startup_timeout=10)
        t.start()
        first = t._proc
        t.start()  # 先停旧进程再起新的
        assert t._proc is not first
        assert first.poll() is not None
        t.stop()

    def test_no_url_raises(self, tmp_path):
        script = tmp_path / "cloudflared"
        script.write_text("#!/bin/sh\necho nothing >&2\nexit 1\n")
        script.chmod(0o755)
        t = Tunnel(local_port=8642, cloudflared_bin=str(script), startup_timeout=3)
        with pytest.raises(TunnelError, match="地址"):
            t.start()

    def test_missing_binary(self, monkeypatch):
        monkeypatch.setattr("codex_quota.tunnel.find_cloudflared", lambda: None)
        t = Tunnel(local_port=8642)
        assert t.available is False
        with pytest.raises(TunnelError, match="cloudflared"):
            t.start()

    def test_stop_without_start_ok(self, fake_cloudflared):
        Tunnel(local_port=8642, cloudflared_bin=fake_cloudflared).stop()  # 不抛异常
