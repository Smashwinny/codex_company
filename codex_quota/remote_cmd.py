"""手机远程命令：ntfy 命令主题收到的文本 → 动作 + 回复正文。

纯逻辑、无 Qt 依赖（在监听线程里运行，也能无头测试）。支持的命令
（大小写/空格不敏感）：
- "url" / "地址"：回推当前访问地址
- "列表" / "list" / "状态"：回推各限流窗口的重置提醒开关状态
- 其他关键词：匹配 provider 名 + 窗口标签/数字（如 kimi5、codex本周）
  或桶名片段（如 spark），切换匹配窗口的重置提醒开关并回推新状态
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from .state import ProviderView, key_excluded, toggle_window, window_keys

_WORD_RE = re.compile(r"[a-z0-9一-鿿]+")


def _norm(s: str) -> str:
    return re.sub(r"[\s·\-_]+", "", s.lower())


def _match_windows(text: str, views: list[ProviderView]) -> list[tuple[str, str]]:
    """关键词 → 命中的 (key, 标签) 列表。

    规则：provider 命中 + 标签/数字命中（"kimi5"→ Kimi 的 5小时），
    或桶名片段（≥4 字符的词，如 spark）单独命中 → 该桶全部窗口。
    """
    ntext = _norm(text)
    matches: list[tuple[str, str]] = []
    for v in views:
        snap = v.state.snapshot
        if snap is None:
            continue
        provider_hit = (_norm(v.name) in ntext or _norm(v.display_name) in ntext
                        or v.name == "zai" and "glm" in ntext)
        provider_all = ntext in {v.name, "glm" if v.name == "zai" else v.name}
        for limit in snap.limits:
            bucket = limit.limit_name or limit.limit_id
            # 桶名按原始分隔符拆词（"GPT-5.3-Codex-Spark"→gpt/5/3/codex/spark）；
            # 去掉 provider 名本身，防止 "codex" 片段全场命中
            parts = [p for p in _WORD_RE.findall(bucket.lower())
                     if len(p) >= 4 and p != _norm(v.name)]
            bucket_hit = any(p in ntext for p in parts)
            for w in (limit.primary, limit.secondary):
                if w is None:
                    continue
                digits = "".join(ch for ch in w.label if ch.isdigit())
                label_hit = (_norm(w.label) in ntext
                             or bool(digits) and digits in ntext)
                if (provider_hit and (label_hit or provider_all)) or bucket_hit:
                    prefix = (v.display_name if limit is snap.primary_limit
                              else f"{v.display_name} · {bucket}")
                    matches.append((f"{v.name}:{bucket}:{w.label}",
                                    f"{prefix} · {w.label}"))
    return matches


def _command_help(views: list[ProviderView]) -> str:
    known = " / ".join(label for _, label in window_keys(views)) or "（暂无数据）"
    return ("可用命令（每次发送一条）：\n"
            "· url / 地址 — 获取当前访问地址\n"
            "· urlrestartcmd / urlrestart / 重连地址 — 重建公网隧道并推送新地址\n"
            "· 列表 / list / 状态 — 查看提醒开关和全部命令\n"
            "· help / 帮助 — 查看全部命令\n"
            "· 关键词切换提醒，如 kimi5、spark、codex本周\n"
            "· provider 名切换其全部提醒，如 codex、kimi、zai；glm 是 zai 的别名\n"
            "· zai5、zai本周、zai本月 — 切换 GLM 对应窗口提醒\n"
            "· 末尾加 on / 开启 或 off / 关闭，明确开启或关闭提醒\n"
            "  例如：zai5 on（开启5小时提醒）、zai off（关闭GLM全部提醒）\n"
            "提醒开关不会重置平台额度；只能操作已有数据的窗口。\n"
            f"当前窗口：{known}")


def handle_command(msg: str, views: list[ProviderView], settings,
                   url: Optional[str] = None,
                   restart_url: Optional[Callable[[], str]] = None) -> tuple[str, str]:
    """处理一条手机命令，返回 (回复正文, 点击跳转URL)。正文空串 = 不回复。"""
    text = _norm(msg)
    if text in {"urlrestartcmd", "urlrestart", "重连地址"}:
        return ((restart_url() if restart_url else "公网隧道未开启，无法重建地址"), "")
    if text in {"url", "地址"}:
        if url:
            return (f"📱 手机访问地址（点通知直接打开）：\n{url}", url)
        return ("手机访问未开启，没有可用地址", "")

    if text in {"列表", "list", "状态"}:
        excludes = set(settings.get("notify_excludes") or [])
        items = window_keys(views)
        if not items:
            return ("暂无额度数据\n\n" + _command_help(views), "")
        lines = [f"{'🔕' if key_excluded(k, excludes) else '🔔'} {label}"
                 for k, label in items]
        return ("重置提醒状态（🔔=推送 / 🔕=不推）：\n" + "\n".join(lines) + "\n\n" + _command_help(views), "")

    desired = None
    for suffix, enabled in (("开启", True), ("关闭", False), ("on", True), ("off", False)):
        if text.endswith(suffix):
            desired = enabled
            text = text[:-len(suffix)]
            break
    matches = [] if text in {"help", "帮助"} else _match_windows(text, views)
    if not matches:
        prefix = "" if text in {"help", "帮助"} else "🤔 没认出命令。\n"
        return (prefix + _command_help(views), "")

    excludes = set(settings.get("notify_excludes") or [])
    all_keys = [k for k, _ in window_keys(views)]
    lines = []
    for key, label in matches:
        was_on = not key_excluded(key, excludes)
        enabled = not was_on if desired is None else desired
        if enabled != was_on:
            excludes = toggle_window(excludes, key, all_keys)
        lines.append(f"{'🔔' if enabled else '🔕'} {label}："
                     f"重置提醒已{'开启' if enabled else '关闭'}")
    settings.set("notify_excludes", sorted(excludes))
    return ("\n".join(lines), "")
