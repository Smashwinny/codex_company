"""codex CLI 自动安装：从 GitHub releases 下载官方独立二进制（免 Node.js）。

- 资产映射：win32→codex-<arch>-pc-windows-msvc.exe.zip；
  darwin→codex-<arch>-apple-darwin.tar.gz；linux→codex-<arch>-unknown-linux-musl.tar.gz
- 装到受管目录 cache_dir()/bin/codex(.exe)，发现逻辑（app_server.find_codex_bin）
  已把该目录列为候选，装完即可用，不动用户 PATH
- 登录仍需用户交互（浏览器授权）：open_login_terminal 在可见终端里跑 codex login
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from typing import Callable, Optional

from .sysdirs import cache_dir

CODEX_RELEASE_BASE = "https://github.com/openai/codex/releases/latest/download"

_ARCH_MAP = {"x86_64": "x86_64", "amd64": "x86_64",
             "aarch64": "aarch64", "arm64": "aarch64"}


class BootstrapError(Exception):
    pass


def managed_codex_path() -> str:
    """我们托管安装的 codex 位置（发现逻辑的候选之一）。"""
    name = "codex.exe" if sys.platform == "win32" else "codex"
    return os.path.join(cache_dir(), "bin", name)


def codex_asset_name(platform: Optional[str] = None,
                     machine: Optional[str] = None) -> str:
    """当前平台对应的 release 资产名。"""
    platform = platform or sys.platform
    import platform as _plat

    arch = _ARCH_MAP.get((machine or _plat.machine()).lower())
    if arch is None:
        raise BootstrapError(f"不支持的架构: {machine or _plat.machine()}")
    if platform == "win32":
        return f"codex-{arch}-pc-windows-msvc.exe.zip"
    if platform == "darwin":
        return f"codex-{arch}-apple-darwin.tar.gz"
    return f"codex-{arch}-unknown-linux-musl.tar.gz"


def download_url() -> str:
    return f"{CODEX_RELEASE_BASE}/{codex_asset_name()}"


def install_codex_cli(progress: Optional[Callable[[str], None]] = None) -> str:
    """下载并解压到受管目录，返回可执行路径。失败抛 BootstrapError。"""
    def _say(msg: str) -> None:
        if progress:
            progress(msg)

    target = managed_codex_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    url = download_url()
    _say("下载中…")
    try:
        from .net import https_context

        with urllib.request.urlopen(url, timeout=120,
                                    context=https_context()) as resp:
            data = resp.read()
    except Exception as exc:
        raise BootstrapError(f"下载失败（网络/被墙？可手动 npm 安装）: {exc}") from exc

    _say("解压中…")
    try:
        _extract(data, url, target)
    except Exception as exc:
        raise BootstrapError(f"解压失败: {exc}") from exc
    if sys.platform != "win32":
        os.chmod(target, 0o755)
    _say("完成")
    return target


def _extract(data: bytes, url: str, target: str) -> None:
    """从 zip/tar.gz 里找出 codex 二进制写到 target。"""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        if url.endswith(".zip"):
            with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
                member = next((n for n in zf.namelist()
                               if n.lower().endswith(".exe")), None)
                if member is None:
                    raise BootstrapError("压缩包里没有 .exe")
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            pkg = os.path.join(tmp, "pkg.tar.gz")
            with open(pkg, "wb") as f:
                f.write(data)
            with tarfile.open(pkg, "r:gz") as tf:
                member = next((m for m in tf.getmembers()
                               if os.path.basename(m.name).startswith("codex")
                               and m.isfile()), None)
                if member is None:
                    raise BootstrapError("压缩包里没有 codex 二进制")
                src = tf.extractfile(member)
                assert src is not None
                with open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)


def open_login_terminal(codex_path: str) -> bool:
    """在可见终端里跑 codex login（登录必须用户交互）。失败返回 False。"""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd.exe", "/c", "start", '"codex login"',
                              codex_path, "login"])
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e",
                              f'tell application "Terminal" to do script '
                              f'"{codex_path} login"'])
        else:
            for term, flag in (("x-terminal-emulator", "-e"),
                               ("gnome-terminal", "--"), ("konsole", "-e"),
                               ("xterm", "-e")):
                if shutil.which(term):
                    subprocess.Popen([term, flag, codex_path, "login"])
                    break
            else:
                return False
        return True
    except OSError:
        return False
