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
