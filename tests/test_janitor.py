"""看门狗与孤儿清扫测试。"""

from __future__ import annotations

from codex_quota.janitor import (
    cleanup_orphans,
    is_our_cloudflared,
    is_our_kimi_web,
)
from codex_quota.tunnel import RestartPolicy


class TestRestartPolicy:
    def test_allows_up_to_max(self):
        p = RestartPolicy(max_attempts=3, window_s=600)
        assert p.allow(now=0) is True
        assert p.allow(now=10) is True
        assert p.allow(now=20) is True
        assert p.allow(now=30) is False  # 超限

    def test_sliding_window_recovers(self):
        p = RestartPolicy(max_attempts=2, window_s=100)
        assert p.allow(now=0) is True
        assert p.allow(now=10) is True
        assert p.allow(now=20) is False
        assert p.allow(now=150) is True  # 窗口滑过，恢复


class TestMatchers:
    def test_our_cloudflared_vendor(self):
        assert is_our_cloudflared([
            "/home/x/codex_company/vendor/bin/cloudflared", "tunnel",
            "--url", "http://127.0.0.1:8642", "--no-autoupdate"]) is True

    def test_our_cloudflared_path_install(self):
        assert is_our_cloudflared([
            "cloudflared", "tunnel", "--url", "http://127.0.0.1:8642",
            "--no-autoupdate"]) is True

    def test_not_our_cloudflared(self):
        # 用户自己的 cloudflared（别的 url / 无 --no-autoupdate）
        assert is_our_cloudflared(["cloudflared", "tunnel", "run", "mytunnel"]) is False
        assert is_our_cloudflared([]) is False

    def test_our_kimi_web(self):
        assert is_our_kimi_web([
            "/home/hulk/.kimi-code/bin/kimi", "web", "--port", "52651",
            "--no-open"]) is True

    def test_user_kimi_web_untouched(self):
        # 用户手动 kimi web（无 --no-open）不杀
        assert is_our_kimi_web(["kimi", "web", "--port", "3080"]) is False
        assert is_our_kimi_web(["kimi"]) is False


class TestCleanupOrphans:
    def test_kills_only_ours(self):
        procs = {
            100: ["/x/vendor/bin/cloudflared", "tunnel", "--url",
                  "http://127.0.0.1:8642", "--no-autoupdate"],
            101: ["kimi", "web", "--port", "52651", "--no-open"],
            102: ["kimi", "web", "--port", "3080"],          # 用户的，不杀
            103: ["chrome", "--some-flag"],                   # 无关进程
        }
        killed = []
        n = cleanup_orphans(
            list_pids=lambda: sorted(procs),
            read_cmdline=lambda pid: procs.get(pid),
            kill=killed.append,
        )
        assert n == 2
        assert sorted(killed) == [100, 101]

    def test_self_pid_skipped(self):
        import os

        procs = {os.getpid(): ["kimi", "web", "--no-open"]}
        killed = []
        n = cleanup_orphans(
            list_pids=lambda: [os.getpid()],
            read_cmdline=lambda pid: procs.get(pid),
            kill=killed.append,
        )
        assert n == 0
        assert killed == []
