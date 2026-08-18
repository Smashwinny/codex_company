"""CLI 模式：一次性输出所有 provider 的额度（Codex / Kimi …）。

用法：
    python -m codex_quota --cli          # 人类可读（进度条 + 倒计时）
    python -m codex_quota --cli --json   # 结构化 JSON（按 provider 分组）

退出码：0 至少一个 provider 成功；2 有「未安装 CLI」类错误；3 全部查询失败。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Optional

from .app_server import (
    CodexNotFoundError,
    QuotaSnapshot,
    QuotaWindow,
    is_logged_in,
    snapshot_to_dict,
)
from .i18n import tr

BAR_WIDTH = 20


def _bar(remaining_percent: Optional[float]) -> str:
    """进度条：填充部分表示剩余额度。"""
    if remaining_percent is None:
        return "?" * BAR_WIDTH
    filled = round(BAR_WIDTH * min(max(remaining_percent, 0), 100) / 100)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _color_flag(remaining_percent: Optional[float]) -> str:
    """与悬浮窗一致的三档阈值（按剩余量）：绿 >30 / 黄 ≤30 / 红 ≤10。"""
    if remaining_percent is None:
        return "?"
    if remaining_percent <= 10:
        return "🔴"
    if remaining_percent <= 30:
        return "🟡"
    return "🟢"


def _fmt_countdown(seconds: Optional[float]) -> str:
    if seconds is None:
        return tr("重置时间未知")
    if seconds <= 0:
        return tr("即将重置")
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return tr("{d} 天 {h} 小时后重置").format(d=days, h=hours)
    if hours:
        return tr("{h} 小时 {m} 分后重置").format(h=hours, m=mins)
    return tr("{m} 分后重置").format(m=mins)


def _fmt_reset_at(reset_at: Optional[float]) -> str:
    if reset_at is None:
        return ""
    t = dt.datetime.fromtimestamp(reset_at).astimezone()
    return f"（{t:%m-%d %H:%M}）"


def _abs_flag(level: Optional[str]) -> str:
    return {"crit": "🔴", "warn": "🟡", "ok": "🟢"}.get(level or "", "?")


def _render_window(w: QuotaWindow, now: float) -> str:
    if w.is_balance:
        return f"{w.label:<6} {w.abs_text or '?':>12}  {_abs_flag(w.abs_level)}"
    rem = w.remaining_percent
    pct = "?" if rem is None else tr("剩 {p}%").format(p=f"{rem:.0f}")
    countdown = _fmt_countdown(w.reset_in_seconds(now)) + _fmt_reset_at(w.reset_at)
    return f"{w.label:<6} {_bar(rem)} {pct:>6}  {_color_flag(rem)} {countdown}"


def render_text(snap: QuotaSnapshot, display_name: str = "Codex") -> str:
    now = snap.fetched_at
    main = snap.primary_limit
    if main is None:
        return tr("{name}：无数据").format(name=display_name)

    plan = tr("套餐: {p}").format(p=snap.plan_type) if snap.plan_type else None
    if plan:
        lines = [tr("{name}（{p}）").format(name=display_name, p=plan)]
    else:
        lines = [display_name]

    lines.append("  " + _render_window(main.primary, now))
    if main.secondary is not None:
        lines.append("  " + _render_window(main.secondary, now))
    if main.credits is not None and main.credits.has_credits:
        c = main.credits
        balance = tr("无限") if c.unlimited else (c.balance or "?")
        lines.append("  " + tr("信用额度余额: {b}").format(b=balance))

    for extra in snap.limits[1:]:
        name = extra.limit_name or extra.limit_id
        lines.append(f"  ── {name} ──")
        lines.append("  " + _render_window(extra.primary, now))
        if extra.secondary is not None:
            lines.append("  " + _render_window(extra.secondary, now))

    ts = dt.datetime.fromtimestamp(snap.fetched_at).astimezone()
    lines.append(tr("更新于 {f}").format(f=f"{ts:%H:%M:%S}"))
    return "\n".join(lines)


def error_hint(message: str) -> Optional[str]:
    """根据错误信息和本地状态给出可操作的引导。"""
    if "未找到 codex" in message or "CODEX_BIN" in message:
        return tr("请先安装 Codex CLI（npm i -g @openai/codex）并运行 codex login")
    if not is_logged_in():
        return tr("未登录：请先运行 codex login")
    if "超时" in message:
        return tr("codex app-server 响应缓慢，稍后点刷新重试")
    return None


def run_cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="codex_quota", description="额度查询（CLI 模式）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--timeout", type=float, default=8.0, help="单 provider 查询超时秒数")
    args = parser.parse_args(argv)

    from .providers import default_providers

    providers = default_providers()
    results: list[tuple[object, QuotaSnapshot]] = []
    errors: list[tuple[object, Exception]] = []
    try:
        for p in providers:
            try:
                results.append((p, p.fetch()))
            except Exception as exc:
                errors.append((p, exc))
    finally:
        for p in providers:
            p.close()  # 释放 kimi web 等保活进程，CLI 是短进程不能留孤儿

    if args.json:
        print(json.dumps({
            "providers": {p.name: snapshot_to_dict(s) for p, s in results},
            "errors": {p.name: str(e) for p, e in errors},
        }, ensure_ascii=False, indent=2))
    else:
        for i, (p, snap) in enumerate(results):
            if i:
                print()
            print(render_text(snap, p.display_name))
        for p, exc in errors:
            hint = error_hint(str(exc))
            msg = f"{p.display_name}: " + tr("查询失败: {e}").format(e=exc)
            print(msg + (f"（{hint}）" if hint else ""), file=sys.stderr)

    if results:
        return 0
    # 全部失败：有「未安装」类错误用 2，其余 3
    if any(isinstance(e, CodexNotFoundError) for _, e in errors):
        return 2
    return 3
