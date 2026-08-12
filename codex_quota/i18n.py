"""极简 i18n：中文字符串即 msgid，tr() 按当前语言返回译文。

语言判定：环境变量 CODEX_QUOTA_LANG（zh/en）优先，否则看 LANG/LC_ALL，
zh* → 中文，其余 → 英文。未知 msgid 原样返回（中文兜底）。
带参数的模板用 str.format：tr("{n} 秒前").format(n=30)。
"""

from __future__ import annotations

import os

_EN: dict[str, str] = {
    # 窗口标签
    "窗口": "?",
    "5小时": "5-hour",
    "本周": "Weekly",
    "{h}小时": "{h}-hour",
    # 倒计时
    "重置时间未知": "Reset time unknown",
    "即将重置": "Resetting soon",
    "{d} 天 {h} 小时后重置": "resets in {d}d {h}h",
    "{h} 小时 {m} 分后重置": "resets in {h}h {m}m",
    "{m} 分后重置": "resets in {m}m",
    # 新鲜度
    "{n} 秒前": "{n}s ago",
    "{n} 分钟前": "{n}m ago",
    "{n} 小时前": "{n}h ago",
    "无数据": "No data",
    # 页脚/状态
    "更新于 {f}": "Updated {f}",
    "⚠ 数据陈旧（更新于 {f}）：{e}": "⚠ Stale (updated {f}): {e}",
    "加载中…": "Loading…",
    # 额度行
    "剩 {p}%": "{p}% left",
    "信用额度余额: {b}": "Credit balance: {b}",
    "无限": "Unlimited",
    # 标题 / CLI
    "⚡ Codex 额度": "⚡ Codex Quota",
    "套餐: {p}": "Plan: {p}",
    "套餐未知": "Plan unknown",
    "Codex 额度（{p}）": "Codex Quota ({p})",
    "Codex 额度：无数据": "Codex Quota: no data",
    # 托盘
    "立即刷新": "Refresh now",
    "显示悬浮窗": "Show widget",
    "隐藏悬浮窗": "Hide widget",
    "开机自启": "Launch at login",
    "退出": "Quit",
    "Codex 额度": "Codex Quota",
    "未知": "unknown",
    "（数据陈旧，更新于 {f}）": "(stale, updated {f})",
    # CLI 错误输出
    "错误: {e}": "Error: {e}",
    "查询失败: {e}": "Query failed: {e}",
    # 错误引导
    "请先安装 Codex CLI（npm i -g @openai/codex）并运行 codex login":
        "Install Codex CLI first (npm i -g @openai/codex), then run codex login",
    "未登录：请先运行 codex login": "Not logged in: run codex login first",
    "codex app-server 响应缓慢，稍后点刷新重试":
        "codex app-server is slow to respond; try refreshing later",
}

_lang_cache: str | None = None


def language() -> str:
    """当前语言：'zh' 或 'en'。"""
    global _lang_cache
    if _lang_cache is not None:
        return _lang_cache
    override = os.environ.get("CODEX_QUOTA_LANG", "").lower()
    if override in ("zh", "en"):
        _lang_cache = override
        return _lang_cache
    locale_str = os.environ.get("LC_ALL") or os.environ.get("LANG") or ""
    _lang_cache = "zh" if locale_str.lower().startswith("zh") else "en"
    return _lang_cache


def set_language(lang: str | None) -> None:
    """显式覆盖语言（主要用于测试）；None 恢复自动检测。"""
    global _lang_cache
    _lang_cache = lang


def tr(msgid: str) -> str:
    if language() == "zh":
        return msgid
    return _EN.get(msgid, msgid)
