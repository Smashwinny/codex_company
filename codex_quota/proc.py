"""跨平台进程原语：detached spawn / 整树回收 / children.pid 追踪。

- spawn_detached：POSIX 用 start_new_session（独立进程组，killpg 一锅端）；
  Windows 用 CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW（与父进程 Ctrl+C 隔离、
  不弹控制台窗口）。creationflags 只能在 win32 分支构造（POSIX 传会 ValueError）。
- kill_tree：POSIX killpg SIGTERM→超时 SIGKILL；Windows taskkill /T /F
  （pid 已死时 taskkill 退出码非 0 属正常，capture_output 且不 check）。
- record_child / sweep_pidfile：spawn 的保活子进程（kimi web / cloudflared）
  记 PID 到 cache_dir()/children.pid，下次启动按此回收——Windows 无 /proc
  可扫，pidfile 是唯一不引入 psutil/WMI 依赖的回收途径；POSIX 下作为
  janitor /proc 扫描之外的叠加保障（幂等）。
- wrap_cmd_shim：npm 全局安装的 codex/kimi 在 Windows 是 .cmd shim，
  CreateProcess 不能直接执行批处理（WinError 193），统一包 cmd.exe /c。
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
from typing import Callable, Optional

from .sysdirs import cache_dir

IS_WINDOWS = sys.platform == "win32"
PIDFILE_NAME = "children.pid"

# 这两个常量在 POSIX 版 Python 的 subprocess 里不存在（AttributeError），
# 用 getattr 兜底字面值使 POSIX 上的单元测试也能构造 win32 分支
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_EXTERNAL_SPAWN_LOCK = threading.RLock()
_PIDFILE_LOCK = threading.RLock()


@contextlib.contextmanager
def external_dll_search_path():
    """让冻结应用启动的外部程序使用系统 DLL 搜索路径。

    PyInstaller Windows bootloader 会把进程级 DLL 目录设为 ``sys._MEIPASS``；
    子进程会继承它，可能让 codex/kimi/cloudflared 误加载 bundle 内的 DLL。
    SetDllDirectoryW 是进程全局状态，必须以同一把锁包住清理、spawn、恢复。
    """
    if not (IS_WINDOWS and getattr(sys, "frozen", False)):
        yield
        return

    import ctypes

    with _EXTERNAL_SPAWN_LOCK:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_dir = kernel32.GetDllDirectoryW
        set_dir = kernel32.SetDllDirectoryW
        get_dir.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
        get_dir.restype = ctypes.c_uint32
        set_dir.argtypes = [ctypes.c_wchar_p]
        set_dir.restype = ctypes.c_int

        size = get_dir(0, None)
        previous = None
        if size:
            buffer = ctypes.create_unicode_buffer(size + 1)
            get_dir(len(buffer), buffer)
            previous = buffer.value
        if not set_dir(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not set_dir(previous):
                raise ctypes.WinError(ctypes.get_last_error())


def popen_external(argv: list[str], **kwargs) -> subprocess.Popen:
    """以不会继承冻结 bundle DLL 目录的环境启动外部进程。"""
    with external_dll_search_path():
        return subprocess.Popen(argv, **kwargs)


def hidden_console_kwargs() -> dict:
    """Windows GUI 进程 spawn 外部命令时抑制控制台黑框的 kwargs（POSIX 为空）。

    统一出口——调用方不要再各自内联 getattr(subprocess, "CREATE_NO_WINDOW", ...)
    （曾出现三处复制且兜底值漂移）。传进 Popen/subprocess.run 即可。
    动态读 sys.platform（不用模块常量），便于测试 monkeypatch 平台。
    """
    return {"creationflags": _CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def run_external(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` 的冻结安全版本。"""
    with external_dll_search_path():
        return subprocess.run(argv, **kwargs)


def wrap_cmd_shim(argv: list[str]) -> list[str]:
    """Windows 上 argv[0] 是 .cmd/.bat 时包 cmd.exe /c；其余原样返回。"""
    if IS_WINDOWS and argv:
        if str(argv[0]).lower().endswith((".cmd", ".bat")):
            return ["cmd.exe", "/c", *argv]
    return argv


def spawn_detached(argv: list[str], *, stdout=None, stderr=None,
                   text: bool = True) -> subprocess.Popen:
    """跨平台"独立进程组"spawn（等价现有 start_new_session=True 语义）。"""
    kwargs = {"stdout": stdout, "stderr": stderr, "text": text}
    if IS_WINDOWS:
        kwargs["creationflags"] = _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return popen_external(wrap_cmd_shim(argv), **kwargs)


def kill_tree(proc: subprocess.Popen, *, timeout: float = 3.0) -> None:
    """终止进程及其整棵子树；已退出的进程直接返回。"""
    if proc is None or proc.poll() is not None:
        return
    if IS_WINDOWS:
        # 无 SIGTERM 等价物；/T 整树 /F 强制。cloudflared/kimi 无控制台
        # 处理器，本来也无法优雅退出，与 POSIX 的 SIGKILL 兜底语义一致
        run_external(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                     capture_output=True, creationflags=_CREATE_NO_WINDOW)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=timeout)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# ---------- children.pid：spawn 追踪 + 启动时回收 ----------

def _pidfile() -> str:
    return os.path.join(cache_dir(), PIDFILE_NAME)


def _windows_process_identity(pid: int) -> Optional[str]:
    """Return the Windows creation-time fingerprint for *pid*.

    A PID alone is unsafe to persist: Windows can reuse it before the next app
    launch.  The kernel creation FILETIME makes a stale record distinguishable
    from an unrelated process that later received the same PID.
    """
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_times = kernel32.GetProcessTimes
        get_times.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
        ]
        get_times.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        # PROCESS_QUERY_LIMITED_INFORMATION: sufficient for GetProcessTimes and
        # available to an unelevated per-user application.
        handle = open_process(0x1000, False, pid)
        if not handle:
            return None
        try:
            created, exited, kernel, user = (FILETIME() for _ in range(4))
            if not get_times(handle, ctypes.byref(created), ctypes.byref(exited),
                             ctypes.byref(kernel), ctypes.byref(user)):
                return None
            ticks = (int(created.high) << 32) | int(created.low)
            return f"{ticks:016x}"
        finally:
            close_handle(handle)
    except (AttributeError, OSError, ValueError):
        return None


def _write_pidfile(lines: list[str]) -> None:
    """Atomically replace the pidfile while the caller holds _PIDFILE_LOCK."""
    path = _pidfile()
    if not lines:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(cache_dir(), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def record_child(pid: int, tag: str) -> None:
    """spawn 成功后立即记录（要在任何阻塞等待输出之前，防崩溃漏记）。"""
    try:
        identity = _windows_process_identity(pid)
        entry = f"{pid} {tag}" + (f" {identity}" if identity else "")
        with _PIDFILE_LOCK:
            try:
                with open(_pidfile(), "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except OSError:
                lines = []
            # A tag denotes one managed service. Reconnects replace its prior
            # record instead of accumulating dead PIDs indefinitely.
            lines = [line for line in lines
                     if len(line.split()) < 2 or line.split()[1] != tag]
            lines.append(entry)
            _write_pidfile(lines)
    except OSError:
        pass  # 记不上只影响孤儿回收，不影响功能


def forget_child(pid: int) -> None:
    """Remove a child after it has been reaped, preventing stale PID reuse."""
    try:
        with _PIDFILE_LOCK:
            try:
                with open(_pidfile(), "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except OSError:
                return
            kept = [line for line in lines
                    if not line.split() or line.split()[0] != str(pid)]
            if kept != lines:
                _write_pidfile(kept)
    except OSError:
        pass


def _default_kill(pid: int) -> None:
    """按 PID 回收：POSIX 下子进程是进程组组长（spawn_detached），killpg 带走整组。"""
    if IS_WINDOWS:
        run_external(["taskkill", "/PID", str(pid), "/T", "/F"],
                     capture_output=True, creationflags=_CREATE_NO_WINDOW)
        return
    os.killpg(pid, signal.SIGTERM)


def sweep_pidfile(*, kill: Optional[Callable[[int], None]] = None) -> int:
    """按 children.pid 回收上轮遗留子进程，返回清理数。kill 可注入（测试）。

    先删文件再逐个杀：杀到一半崩溃也不会留下重复条目（重复杀死 pid 无害）。
    """
    validate_identity = kill is None and IS_WINDOWS
    kill = kill or _default_kill
    with _PIDFILE_LOCK:
        try:
            with open(_pidfile(), "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            return 0
        try:
            os.remove(_pidfile())
        except OSError:
            pass
    killed = 0
    for line in lines:
        parts = line.split()
        if not parts or not parts[0].isdigit():
            continue
        if validate_identity:
            # Legacy two-field records cannot safely identify a Windows
            # process. Skip them rather than risk killing a reused unrelated
            # PID. New records always carry the creation-time fingerprint.
            if len(parts) < 3:
                continue
            if _windows_process_identity(int(parts[0])) != parts[2]:
                continue
        try:
            kill(int(parts[0]))
            killed += 1
        except Exception:
            pass  # 死 pid / PID 复用到无关进程时 taskkill 失败，均无无害
    return killed
