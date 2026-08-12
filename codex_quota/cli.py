"""CLI 模式：一次性输出 Codex 额度。

用法：
    python -m codex_quota --cli          # 人类可读（进度条 + 倒计时）
    python -m codex_quota --cli --json   # 结构化 JSON

退出码：0 成功；2 未安装 codex；3 查询失败（未登录/超时/协议错误）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Optional

from .app_server import (
    AppServerClient,
    AppServerError,
    CodexNotFoundError,
    QuotaSnapshot,
    QuotaWindow,
    find_codex_bin,
    is_logged_in,
    snapshot_to_dict,
)

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
        return "重置时间未知"
    if seconds <= 0:
        return "即将重置"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days} 天 {hours} 小时后重置"
    if hours:
        return f"{hours} 小时 {mins} 分后重置"
    return f"{mins} 分后重置"


def _fmt_reset_at(reset_at: Optional[float]) -> str:
    if reset_at is None:
        return ""
    t = dt.datetime.fromtimestamp(reset_at).astimezone()
    return f"（{t:%m-%d %H:%M}）"


def _render_window(w: QuotaWindow, now: float) -> str:
    rem = w.remaining_percent
    pct = "?" if rem is None else f"剩 {rem:.0f}%"
    countdown = _fmt_countdown(w.reset_in_seconds(now)) + _fmt_reset_at(w.reset_at)
    return f"{w.label:<6} {_bar(rem)} {pct:>6}  {_color_flag(rem)} {countdown}"


def render_text(snap: QuotaSnapshot) -> str:
    now = snap.fetched_at
    main = snap.primary_limit
    if main is None:
        return "Codex 额度：无数据"

    plan = f"套餐: {snap.plan_type}" if snap.plan_type else "套餐未知"
    lines = [f"Codex 额度（{plan}）"]

    lines.append("  " + _render_window(main.primary, now))
    if main.secondary is not None:
        lines.append("  " + _render_window(main.secondary, now))
    if main.credits is not None and main.credits.has_credits:
        c = main.credits
        balance = "无限" if c.unlimited else (c.balance or "?")
        lines.append(f"  信用额度余额: {balance}")

    for extra in snap.limits[1:]:
        name = extra.limit_name or extra.limit_id
        lines.append(f"  ── {name} ──")
        lines.append("  " + _render_window(extra.primary, now))
        if extra.secondary is not None:
            lines.append("  " + _render_window(extra.secondary, now))

    ts = dt.datetime.fromtimestamp(snap.fetched_at).astimezone()
    lines.append(f"更新于 {ts:%H:%M:%S}")
    return "\n".join(lines)


def error_hint(message: str) -> Optional[str]:
    """根据错误信息和本地状态给出可操作的引导。"""
    if "未找到 codex" in message or "CODEX_BIN" in message:
        return "请先安装 Codex CLI（npm i -g @openai/codex）并运行 codex login"
    if not is_logged_in():
        return "未登录：请先运行 codex login"
    if "超时" in message:
        return "codex app-server 响应缓慢，稍后点刷新重试"
    return None


def run_cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="codex_quota", description="Codex 额度查询（CLI 模式）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--timeout", type=float, default=8.0, help="app-server 响应超时秒数")
    args = parser.parse_args(argv)

    try:
        client = AppServerClient(codex_bin=find_codex_bin(), timeout=args.timeout)
        snap = client.read_rate_limits()
    except CodexNotFoundError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except AppServerError as exc:
        hint = error_hint(str(exc))
        print(f"查询失败: {exc}" + (f"（{hint}）" if hint else ""), file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(snapshot_to_dict(snap), ensure_ascii=False, indent=2))
    else:
        print(render_text(snap))
    return 0
