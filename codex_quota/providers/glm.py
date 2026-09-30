"""GLM（智谱）Coding Plan 额度 provider（预设类型）。

GET https://open.bigmodel.cn/api/monitor/usage/quota/limit
Authorization: <裸 API key>（该端点不吃 Bearer 前缀，实测加 Bearer 401）
团队版：URL 加 ?type=2，并带 bigmodel-organization / bigmodel-project 请求头。

响应（实测）：
{"code": 200, "msg": "操作成功",
 "data": {"limits": [
     {"type": "TOKENS_LIMIT", "unit": 3, "percentage": 7.0, "nextResetTime": 1784150654841},
     {"type": "TOKENS_LIMIT", "unit": 6, "percentage": 33.0, "nextResetTime": 1784150654841}],
   "level": "pro"},
 "success": true}

字段：unit 3=5小时窗口 / 6=每周窗口；percentage=已用百分比；nextResetTime=Unix 毫秒。
窗口分类优先级（对齐 cc-switch parse_zhipu_token_tiers / sub2api，issue #3036）：
1. 显式 unit 字段——不能用 reset 排序代替（周期末尾周窗口可能比 5h 更早重置）
2. unit 缺失/未识别：无 nextResetTime 的条目归 5h（0% 状态下 5h 桶可能无 reset），
   其余按 reset 升序依次填仍空缺的槽
CREDIT_LIMIT（信用额度）仅作无任何 TOKENS_LIMIT 时的降级展示；TIME_LIMIT（MCP
月度额度）度量不同，不参与窗口分类。

仅限 Coding Plan 订阅用户：普通按量计费 key 返回「当前用户不存在coding plan」。
api_key 支持 "$ENV_VAR" 引用环境变量；团队版 organization/project 可在
providers.toml 里同节配置（托盘对话框仅管理 key）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from ..app_server import QuotaSnapshot, QuotaWindow, RateLimit
from .config import resolve_secret

QUOTA_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"
ZAI_QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
WINDOW_5H_MINUTES = 300
WINDOW_WEEK_MINUTES = 10080

UNIT_TO_WINDOW = {3: WINDOW_5H_MINUTES, 6: WINDOW_WEEK_MINUTES}


class GLMError(Exception):
    pass


def _parse_reset_ms(value: Any) -> Optional[float]:
    """nextResetTime → Unix 秒。兼容数字（毫秒/秒）与 ISO8601 字符串。"""
    if isinstance(value, (int, float)) and value > 0:
        ms = float(value)
        return ms / 1000.0 if ms >= 1_000_000_000_000 else ms
    if isinstance(value, str) and value:
        from ..providers.kimi import _parse_iso8601
        return _parse_iso8601(value)
    return None


def parse_glm_quota(payload: dict[str, Any], now: Optional[float] = None) -> QuotaSnapshot:
    """把 /api/monitor/usage/quota/limit 的响应映射为 QuotaSnapshot。"""
    code = payload.get("code")
    if code not in (None, 200) or payload.get("success") is False:
        msg = payload.get("msg") or f"code={code!r}"
        raise GLMError(f"GLM 额度接口返回异常：{msg}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise GLMError("GLM 额度接口返回异常（缺少 data）")

    entries: list[dict[str, Any]] = []
    credit_fallback: list[dict[str, Any]] = []
    for item in data.get("limits") or []:
        if not isinstance(item, dict):
            continue
        limit_type = str(item.get("type") or "").upper()
        if limit_type == "TOKENS_LIMIT":
            entries.append(item)
        elif limit_type == "CREDIT_LIMIT":
            credit_fallback.append(item)
    if not entries and credit_fallback:
        entries = credit_fallback
    if not entries:
        raise GLMError("GLM 额度接口未返回任何限额窗口")

    def _to_window(item: dict[str, Any], minutes: Optional[int]) -> QuotaWindow:
        pct = item.get("percentage")
        used = float(pct) if isinstance(pct, (int, float)) else None
        return QuotaWindow(
            used_percent=used,
            window_minutes=minutes,
            reset_at=_parse_reset_ms(item.get("nextResetTime")),
        )

    five_h: Optional[QuotaWindow] = None
    weekly: Optional[QuotaWindow] = None
    unclassified: list[dict[str, Any]] = []
    for item in entries:
        minutes = UNIT_TO_WINDOW.get(item.get("unit"))
        if minutes == WINDOW_5H_MINUTES and five_h is None:
            five_h = _to_window(item, minutes)
        elif minutes == WINDOW_WEEK_MINUTES and weekly is None:
            weekly = _to_window(item, minutes)
        else:
            unclassified.append(item)

    # unit 缺失/未识别：无 reset 的归 5h，其余按 reset 升序填剩余槽位
    unclassified.sort(key=lambda i: _parse_reset_ms(i.get("nextResetTime")) or 0.0)
    for item in unclassified:
        if five_h is None:
            five_h = _to_window(item, WINDOW_5H_MINUTES)
        elif weekly is None:
            weekly = _to_window(item, WINDOW_WEEK_MINUTES)

    # 与 Kimi 分区一致：primary=本周，secondary=5小时
    primary = weekly or five_h or QuotaWindow()
    secondary = five_h if weekly is not None else None
    level = data.get("level")
    rl = RateLimit(limit_id="glm", plan_type=level, primary=primary,
                   secondary=secondary)
    return QuotaSnapshot(
        fetched_at=now if now is not None else time.time(),
        plan_type=level,
        limits=[rl],
        provider="glm",
    )


class GLMProvider:
    name = "glm"

    def __init__(self, api_key: Optional[str] = None, *,
                 display_name: str = "GLM",
                 base_url: str = QUOTA_URL, timeout: float = 8.0,
                 organization: Optional[str] = None,
                 project: Optional[str] = None):
        self._api_key = api_key
        self.display_name = display_name
        self._base_url = base_url
        self._timeout = timeout
        self._organization = organization
        self._project = project

    def fetch(self) -> QuotaSnapshot:
        key = resolve_secret(self._api_key)
        if not key:
            raise GLMError("未配置 GLM API key（托盘 → 管理额度来源 中填写）")
        url = self._base_url
        headers: dict[str, str] = {
            "Authorization": key,  # 裸 key，无 Bearer 前缀
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._organization:
            # 团队版 Coding Plan：不加 type=2 官方回「当前用户不存在coding plan」
            sep = "&" if urllib.parse.urlparse(url).query else "?"
            url = f"{url}{sep}type=2"
            headers["bigmodel-organization"] = self._organization
            if self._project:
                headers["bigmodel-project"] = self._project
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise GLMError("GLM API key 无效（401/403），请检查") from exc
            raise GLMError(f"GLM 接口返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise GLMError("GLM 接口无法连接（检查网络）") from exc
        return parse_glm_quota(payload)

    def close(self) -> None:
        pass  # 无长驻资源
