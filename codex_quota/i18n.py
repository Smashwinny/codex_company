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
    "余额": "Balance",
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
    "⚠ 数据陈旧 · 更新于 {f}": "⚠ Stale · updated {f}",
    "⚠ 数据陈旧（更新于 {f}）：{e}": "⚠ Stale (updated {f}): {e}",
    "加载中…": "Loading…",
    # 额度行
    "剩 {p}%": "{p}% left",
    "信用额度余额: {b}": "Credit balance: {b}",
    "无限": "Unlimited",
    # 标题 / CLI
    "⚡ Codex 额度": "⚡ Codex Quota",
    "⚡ 额度监控": "⚡ Quota Monitor",
    "套餐: {p}": "Plan: {p}",
    "套餐未知": "Plan unknown",
    "Codex 额度（{p}）": "Codex Quota ({p})",
    "Codex 额度：无数据": "Codex Quota: no data",
    "{name}（{p}）": "{name} ({p})",
    "{name}：无数据": "{name}: no data",
    # 托盘
    "立即刷新": "Refresh now",
    "显示悬浮窗": "Show widget",
    "隐藏悬浮窗": "Hide widget",
    "开机自启": "Launch at login",
    "复制手机访问地址": "Copy phone access URL",
    "手机与电脑同一局域网，浏览器打开即看": "Open in phone browser on the same LAN",
    "推送访问地址到手机": "Push access URL to phone",
    "通过 ntfy 推送网页地址，手机点通知直接打开":
        "Push the web URL via ntfy — tap the notification to open it",
    "推送失败（网络或 ntfy 服务不可达）": "Push failed (network or ntfy unreachable)",
    "未开启 ntfy 通知，无法推送": "ntfy notify is off — cannot push",
    "未开启手机访问，没有可推送的地址": "Phone access is off — no URL to push",
    "手机通知（ntfy）订阅指引": "Phone notify (ntfy) guide",
    "查看/复制订阅主题、命令主题，发送测试推送":
        "View/copy topics and send a test push",
    "未开启 ntfy 通知": "ntfy notify is off",
    "此框只在首次生成主题时自动弹出一次；之后可从托盘菜单再次打开":
        "Shown automatically once when the topic is first created; reopen anytime from the tray menu",
    "① 订阅（收额度重置 / 访问地址推送）":
        "1. Subscribe (quota-reset / access-URL pushes)",
    "手机安装 ntfy App → 添加订阅下面这个主题；或在手机浏览器直接打开订阅链接，按提示跳转 App":
        "Install the ntfy app on your phone → subscribe to the topic below; or open the subscribe link in your phone browser and follow the prompt",
    "主题": "Topic",
    "订阅链接": "Subscribe URL",
    "② 反向触发（想用手机看时，主动要地址）":
        "2. Trigger from phone (ask for the URL on demand)",
    "在 ntfy App 里再订阅下面的命令主题。想用手机看仪表盘时向它发送 url ——电脑回推当前地址，点通知直达网页；发送 列表 查看各额度提醒开关；发送 kimi5、spark 这类关键词可直接开/关对应窗口的重置提醒":
        "Also subscribe to the command topic below. Send 'url' to get the current access URL (tap to open); send '列表' to see alert switches; send keywords like kimi5/spark to toggle a window's reset alert",
    "命令主题": "Command topic",
    "③ 验证": "3. Verify",
    "发送测试推送": "Send test push",
    "手机上应立刻收到一条通知；收不到说明订阅没配对":
        "Your phone should get a notification right away; if not, the subscription isn't set up",
    "🔔 测试推送：订阅成功！额度重置、手机访问地址都会推到这里。":
        "🔔 Test push: subscription works! Quota resets and access URLs will arrive here.",
    "已发送 ✓（看手机）": "Sent ✓ (check phone)",
    "发送失败（检查网络/ntfy 服务）": "Send failed (check network/ntfy)",
    "复制": "Copy",
    "完成": "Done",
    # 告警阈值
    "告警阈值": "Alert thresholds",
    "敏感": "sensitive",
    "默认": "default",
    "宽松": "relaxed",
    "{w} / {c}（{tag}）": "{w} / {c} ({tag})",
    "剩余量 ≤ {w}% 显示黄色，≤ {c}% 显示红色":
        "≤ {w}% left shows yellow, ≤ {c}% shows red",
    # 取色来源
    "主模型显示": "Primary model display",
    "重置提醒": "Reset alerts",
    "勾选的额度桶回满 100% 时推送手机通知":
        "Checked buckets push to phone when refilled to 100%",
    "（暂无数据）": "(no data yet)",
    "勾选参与取色，图标按勾选项的最低剩余量变色":
        "Checked items set the icon color (worst remaining wins)",
    # 首启向导 / 自检
    "codex-quota 初始设置": "codex-quota Setup",
    "检测运行环境，缺失项一键复制修复命令":
        "Checking your environment — copy fix commands with one click",
    "环境检测": "Environment check",
    "可选功能": "Optional features",
    "全部重新检测": "Re-check all",
    "复制命令": "Copy command",
    "已复制": "Copied",
    "完成并启动": "Finish & Start",
    "跳过": "Skip",
    "手机访问（局域网 + 公网隧道）": "Phone access (LAN + public tunnel)",
    "额度重置推送（ntfy）": "Quota reset push (ntfy)",
    "初始设置 / 环境自检": "Setup / Environment check",
    # provider 管理
    "管理额度来源": "Manage providers",
    "本地工具": "Local tools",
    "云端服务（API key）": "Cloud services (API key)",
    "Codex（本地 codex CLI）": "Codex (local codex CLI)",
    "Kimi（本地 kimi CLI）": "Kimi (local kimi CLI)",
    "Claude Code（本地登录凭证）": "Claude Code (local credentials)",
    "手动余额（免 key）": "Manual balance (no key)",
    "✓ 已检测到 dsh 凭证：DeepSeek 余额正在自动查询，手动余额已自动停用":
        "✓ dsh credentials detected: DeepSeek balance auto-fetched, manual entry disabled",
    "未检测到 dsh 凭证：可勾选并手填余额":
        "No dsh credentials detected: enable to enter balance manually",
    "手动余额（适合网页版用户，定期手填）":
        "Manual balance (for web users, fill in periodically)",
    "显示名称": "Display name",
    "当前余额，如 23.5": "Current balance, e.g. 23.5",
    "上次填写：{t}": "Last filled: {t}",
    "从未填写": "Never filled",
    "手动余额": "Manual balance",
    "API key（sk-… 或 $环境变量）": "API key (sk-… or $ENV_VAR)",
    "测试连接": "Test connection",
    "✓ 已检测到 dsh 凭证，key 留空即可自动使用":
        "✓ dsh credentials detected, leave key empty to auto-use",
    "测试中…": "Testing…",
    "✅ 连接成功：余额 {t}": "✅ Connected: balance {t}",
    "保存": "Save",
    "取消": "Cancel",
    "已安装（{v}）": "Installed ({v})",
    "已安装": "Installed",
    "已登录": "Logged in",
    "未安装": "Not installed",
    "未登录": "Not logged in",
    "Codex 登录": "Codex login",
    "未安装（可选，仅不显示 Kimi 分区）": "Not installed (optional, Kimi section hidden)",
    "未找到（已装过就设 CODEX_BIN 指向 codex.cmd 的完整路径）":
        "Not found (if installed, set CODEX_BIN to the full path of codex.cmd)",
    "未安装（可选，手机仅局域网可看；运行 install.sh 下载）":
        "Not installed (optional, phone view limited to LAN; run install.sh)",
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
