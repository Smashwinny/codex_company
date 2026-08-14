"""cloudflared 隧道模块测试（用假脚本，不依赖真实 cloudflared/网络）。"""

from __future__ import annotations

import os

import pytest

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
