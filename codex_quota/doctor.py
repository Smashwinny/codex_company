"""运行环境自检：输出各依赖的状态与修复建议。

Qt 无关——首启向导（ui/wizard.py）和未来的 --doctor 命令共用。
每个 CheckItem 带可选的修复命令，界面层配"复制命令"按钮即可。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from .i18n import tr

OK = "ok"      # 正常
WARN = "warn"  # 可选项缺失（功能降级）
FAIL = "fail"  # 必需项缺失（核心功能不可用）


@dataclass
class CheckItem:
    key: str
    name: str
    required: bool
    status: str                     # OK / WARN / FAIL
    detail: str
    fix_command: Optional[str] = None


def _default_version(bin_path: str) -> Optional[str]:
    """执行 <bin> --version 取首行输出；失败返回 None。"""
    try:
        from .proc import run_external, wrap_cmd_shim

        kwargs = {"capture_output": True, "text": True, "timeout": 5}
        if sys.platform == "win32":
            # 首启向导会在 GUI 进程里执行 codex.cmd --version；
            # 显式禁用控制台窗口，避免 cmd.exe 短暂黑窗闪现。
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0x08000000)
        out = run_external(wrap_cmd_shim([bin_path, "--version"]), **kwargs)
        lines = (out.stdout or out.stderr).strip().splitlines()
        return lines[0].strip() if lines else None
    except Exception:
        return None


def run_checks(version_of: Callable[[str], Optional[str]] = _default_version
               ) -> list[CheckItem]:
    from .app_server import CodexNotFoundError, find_codex_bin, is_logged_in
    from .providers.kimi import find_kimi_bin
    from .tunnel import find_cloudflared

    items: list[CheckItem] = []

    # --- Codex CLI（必需） ---
    try:
        codex = find_codex_bin()
        ver = version_of(codex) or "?"
        items.append(CheckItem("codex_bin", "Codex CLI", True, OK,
                               tr("已安装（{v}）").format(v=ver)))
        if is_logged_in():
            items.append(CheckItem("codex_login", tr("Codex 登录"), True, OK,
                                   tr("已登录")))
        else:
            items.append(CheckItem("codex_login", tr("Codex 登录"), True, FAIL,
                                   tr("未登录"), "codex login"))
    except CodexNotFoundError:
        items.append(CheckItem("codex_bin", "Codex CLI", True, FAIL,
                               tr("未安装"),
                               "npm i -g @openai/codex && codex login"))

    # --- Kimi CLI（可选） ---
    if find_kimi_bin():
        items.append(CheckItem("kimi_bin", "Kimi CLI", False, OK, tr("已安装")))
    else:
        items.append(CheckItem("kimi_bin", "Kimi CLI", False, WARN,
                               tr("未安装（可选，仅不显示 Kimi 分区）")))

    # --- cloudflared（可选，手机公网访问） ---
    if find_cloudflared():
        items.append(CheckItem("cloudflared", "cloudflared", False, OK,
                               tr("已安装")))
    else:
        items.append(CheckItem("cloudflared", "cloudflared", False, WARN,
                               tr("未安装（可选，手机仅局域网可看；运行 install.sh 下载）")))
    return items


def has_failures(items: list[CheckItem]) -> bool:
    return any(i.status == FAIL for i in items)
