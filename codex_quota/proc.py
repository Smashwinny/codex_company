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

import os
import signal
import subprocess
import sys
from typing import Callable, Optional

from .sysdirs import cache_dir

IS_WINDOWS = sys.platform == "win32"
PIDFILE_NAME = "children.pid"

# 这两个常量在 POSIX 版 Python 的 subprocess 里不存在（AttributeError），
# 用 getattr 兜底字面值使 POSIX 上的单元测试也能构造 win32 分支
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


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
    return subprocess.Popen(wrap_cmd_shim(argv), **kwargs)


def kill_tree(proc: subprocess.Popen, *, timeout: float = 3.0) -> None:
    """终止进程及其整棵子树；已退出的进程直接返回。"""
    if proc is None or proc.poll() is not None:
        return
    if IS_WINDOWS:
        # 无 SIGTERM 等价物；/T 整树 /F 强制。cloudflared/kimi 无控制台
        # 处理器，本来也无法优雅退出，与 POSIX 的 SIGKILL 兜底语义一致
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
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


def record_child(pid: int, tag: str) -> None:
    """spawn 成功后立即记录（要在任何阻塞等待输出之前，防崩溃漏记）。"""
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        with open(_pidfile(), "a", encoding="utf-8") as f:
            f.write(f"{pid} {tag}\n")
    except OSError:
        pass  # 记不上只影响孤儿回收，不影响功能


def _default_kill(pid: int) -> None:
    """按 PID 回收：POSIX 下子进程是进程组组长（spawn_detached），killpg 带走整组。"""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True)
        return
    os.killpg(pid, signal.SIGTERM)


def sweep_pidfile(*, kill: Optional[Callable[[int], None]] = None) -> int:
    """按 children.pid 回收上轮遗留子进程，返回清理数。kill 可注入（测试）。

    先删文件再逐个杀：杀到一半崩溃也不会留下重复条目（重复杀死 pid 无害）。
    """
    kill = kill or _default_kill
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
        try:
            kill(int(parts[0]))
            killed += 1
        except Exception:
            pass  # 死 pid / PID 复用到无关进程时 taskkill 失败，均无无害
    return killed
