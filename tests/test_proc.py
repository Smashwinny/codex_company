"""proc.py 跨平台进程原语测试。

POSIX 分支真 spawn 真杀（本机即 Linux）；Windows 分支用 monkeypatch
IS_WINDOWS + 注入/捕获子进程调用验证参数构造，不真执行。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from codex_quota import proc
from codex_quota.sysdirs import cache_dir


class TestWrapCmdShim:
    def test_passthrough_on_posix(self):
        assert proc.wrap_cmd_shim(["/usr/bin/codex", "app-server"]) == \
            ["/usr/bin/codex", "app-server"]

    def test_cmd_wrapped_on_windows(self, monkeypatch):
        monkeypatch.setattr(proc, "IS_WINDOWS", True)
        out = proc.wrap_cmd_shim([r"C:\npm\codex.cmd", "app-server"])
        assert out[:2] == ["cmd.exe", "/c"]
        assert out[2:] == [r"C:\npm\codex.cmd", "app-server"]

    def test_exe_not_wrapped_on_windows(self, monkeypatch):
        monkeypatch.setattr(proc, "IS_WINDOWS", True)
        argv = [r"C:\bin\cloudflared.exe", "tunnel"]
        assert proc.wrap_cmd_shim(argv) == argv


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 真 spawn 测试")
class TestPosixLifecycle:
    def test_spawn_detached_new_process_group(self):
        import os

        p = proc.spawn_detached(["sleep", "30"], text=False)
        try:
            assert os.getpgid(p.pid) == p.pid  # 独立进程组组长
        finally:
            proc.kill_tree(p, timeout=1)
        assert p.poll() is not None

    def test_kill_tree_kills_group(self):
        """组长的孙进程也被带走（sh -c 派生子进程）。"""
        import os

        p = proc.spawn_detached(["sh", "-c", "sleep 30"], text=False)
        child = int(subprocess.check_output(
            ["pgrep", "-P", str(p.pid), "sleep"], text=True).strip())
        proc.kill_tree(p, timeout=1)
        assert p.poll() is not None
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)  # 孙进程已死


class TestPidfile:
    def test_record_and_sweep(self):
        fake_killed: list[int] = []
        proc.record_child(424242, "kimi-web")
        proc.record_child(434343, "cloudflared")
        path = cache_dir() + "/children.pid"
        with open(path) as f:
            content = f.read()
        assert "424242 kimi-web" in content
        assert "434343 cloudflared" in content

        n = proc.sweep_pidfile(kill=fake_killed.append)
        assert n == 2
        assert fake_killed == [424242, 434343]
        import os
        assert not os.path.exists(path)  # sweep 后文件已清

    def test_sweep_missing_file_returns_zero(self):
        assert proc.sweep_pidfile(kill=lambda pid: None) == 0

    def test_record_dedupes_by_pid_not_tag(self):
        """同 tag 不同 PID 都保留：kill 未生效的幸存者不能被同 tag 替换脱管。"""
        import os

        proc.record_child(111111, "cloudflared")
        proc.record_child(222222, "cloudflared")
        path = os.path.join(cache_dir(), "children.pid")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "111111 cloudflared" in content
        assert "222222 cloudflared" in content

        # 同 PID 重复记录才替换；forget 按 PID 精确移除
        proc.record_child(222222, "cloudflared")
        with open(path, encoding="utf-8") as f:
            assert f.read().count("222222") == 1
        proc.forget_child(111111)
        proc.forget_child(222222)
        assert not os.path.exists(path)

    def test_windows_sweep_skips_reused_pid(self, monkeypatch):
        import os

        path = os.path.join(cache_dir(), "children.pid")
        os.makedirs(cache_dir(), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("777 cloudflared original-creation\n")
        killed = []
        monkeypatch.setattr(proc, "IS_WINDOWS", True)
        monkeypatch.setattr(proc, "_windows_process_identity",
                            lambda _pid: "replacement-creation")
        monkeypatch.setattr(proc, "_default_kill", killed.append)

        assert proc.sweep_pidfile() == 0
        assert killed == []

    def test_sweep_tolerates_bad_lines_and_dead_pids(self):
        import os

        path = os.path.join(cache_dir(), "children.pid")
        os.makedirs(cache_dir(), exist_ok=True)
        with open(path, "w") as f:
            f.write("not-a-pid\n\n555555 cloudflared\n")
        killed: list[int] = []

        def kill(pid):
            if pid == 555555:
                raise ProcessLookupError  # 死 pid 不炸、不计数
            killed.append(pid)

        assert proc.sweep_pidfile(kill=kill) == 0


class TestWindowsBranches:
    def test_spawn_uses_creationflags(self, monkeypatch):
        monkeypatch.setattr(proc, "IS_WINDOWS", True)
        captured = {}

        class FakePopen:
            def __init__(self, argv, **kw):
                captured["argv"] = argv
                captured["kw"] = kw

        monkeypatch.setattr(proc.subprocess, "Popen", FakePopen)
        proc.spawn_detached(["x.cmd", "web"], stdout=None)
        assert captured["argv"][0] == "cmd.exe"  # shim 已包装
        flags = captured["kw"]["creationflags"]
        assert flags & proc._CREATE_NEW_PROCESS_GROUP
        assert flags & proc._CREATE_NO_WINDOW
        assert "start_new_session" not in captured["kw"]

    def test_kill_tree_windows_uses_taskkill(self, monkeypatch):
        monkeypatch.setattr(proc, "IS_WINDOWS", True)
        calls = []

        class FakeProc:
            pid = 777
            def poll(self): return None
            def wait(self, timeout=None): return 0

        def fake_run(cmd, **kw):
            calls.append((cmd, kw))
            return None

        monkeypatch.setattr(proc.subprocess, "run", fake_run)
        proc.kill_tree(FakeProc())
        cmd, kw = calls[0]
        assert cmd == ["taskkill", "/PID", "777", "/T", "/F"]
        assert kw.get("capture_output")  # 死 pid 退出码非 0，必须吞输出不 check
        assert kw["creationflags"] & proc._CREATE_NO_WINDOW


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX /proc 身份校验实测")
class TestSweepPosixIdentity:
    def test_skips_pid_whose_cmdline_lacks_keyword(self):
        """PID 存活但 cmdline 不含 tag 关键词（PID 复用）→ 不杀。"""
        import os
        import signal as _sig
        import subprocess as sp

        sleeper = sp.Popen(["sleep", "30"], start_new_session=True)
        try:
            proc.record_child(sleeper.pid, "cloudflared")
            n = proc.sweep_pidfile()  # 真实 kill，但应被身份校验拦下
            assert n == 0
            assert sleeper.poll() is None  # 还活着 = 未被误杀
        finally:
            try:
                os.killpg(sleeper.pid, _sig.SIGKILL)
            except ProcessLookupError:
                pass

    def test_kills_process_matching_keyword(self, tmp_path):
        """cmdline 含 tag 关键词（真孤儿）→ 正常回收。"""
        import shutil
        import subprocess as sp

        fake = tmp_path / "cloudflared-fake"
        shutil.copy("/bin/sleep", fake)
        orphan = sp.Popen([str(fake), "30"], start_new_session=True)
        proc.record_child(orphan.pid, "cloudflared")
        assert proc.sweep_pidfile() == 1
        orphan.wait(timeout=3)
        assert orphan.poll() is not None  # 已被回收


class TestWindowsIdentityCheck:
    def test_legacy_two_field_record_image_match(self, monkeypatch):
        """Windows 两字段遗留记录：tasklist 镜像名匹配才杀。"""
        import subprocess as sp

        monkeypatch.setattr(proc, "IS_WINDOWS", True)

        def fake_run_external(argv, **kw):
            pid = argv[2].split()[-1]  # ["tasklist", "/FI", "PID eq <pid>", ...]
            if pid == "1234":
                return sp.CompletedProcess(argv, 0, stdout='"cloudflared.exe","1234","Console","1","10,000 K"\n')
            return sp.CompletedProcess(argv, 0, stdout='"chrome.exe","5678","Console","1","10,000 K"\n')

        monkeypatch.setattr(proc, "run_external", fake_run_external)
        assert proc._pid_matches_tag_windows(1234, "cloudflared") is True
        assert proc._pid_matches_tag_windows(5678, "cloudflared") is False  # PID 被 chrome 复用
        assert proc._pid_matches_tag_windows(1234, "unknown-tag") is False
