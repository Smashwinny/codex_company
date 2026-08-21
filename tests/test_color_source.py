"""窗口键与取色排除（window_keys / key_excluded / toggle_window / worst_remaining）。

仅用 duck-typed 视图对象，不实例化 Qt 控件，可无头运行。
"""

from __future__ import annotations

from types import SimpleNamespace

from codex_quota.state import (
    key_excluded,
    toggle_window,
    window_keys,
)
from codex_quota.ui.tray import worst_remaining
from tests.conftest import codex_snapshot


def _view(name="codex", display="Codex", snap=None):
    return SimpleNamespace(name=name, display_name=display,
                           state=SimpleNamespace(snapshot=snap))


def _main_bucket(snap):
    main = snap.primary_limit
    return main.limit_name or main.limit_id


def test_window_keys_are_window_level():
    keys = [k for k, _ in window_keys([_view(snap=codex_snapshot())])]
    main_bucket = _main_bucket(codex_snapshot())
    assert f"codex:{main_bucket}:本周" in keys
    assert f"codex:GPT-5.3-Codex-Spark:本周" in keys
    assert all(k.count(":") == 2 for k in keys)  # provider:桶:窗口 三段式


def test_worst_includes_all_by_default():
    views = [_view(snap=codex_snapshot())]
    assert worst_remaining(views) == 9.0  # 主限额最差（Spark 98%）


def test_excludes_bucket_prefix_and_window():
    views = [_view(snap=codex_snapshot())]
    main_bucket = _main_bucket(codex_snapshot())
    # 桶级前缀：排除主限额桶 → 只剩 Spark 的 98%
    assert worst_remaining(views, frozenset({f"codex:{main_bucket}"})) == 98.0
    # 窗口级：排除 Spark:本周 不影响最差值
    assert worst_remaining(
        views, frozenset({"codex:GPT-5.3-Codex-Spark:本周"})) == 9.0
    # 全部排除 → 无数据
    all_keys = [k for k, _ in window_keys(views)]
    assert worst_remaining(views, frozenset(all_keys)) is None


def test_key_excluded_prefix_compat():
    """旧版桶级配置（provider:桶）前缀覆盖其下所有窗口。"""
    assert key_excluded("codex:Spark:本周", {"codex:Spark"})
    assert key_excluded("codex:Spark:本周", {"codex:Spark:本周"})
    assert not key_excluded("codex:Spark:本周", {"codex:Spark:5小时"})
    assert not key_excluded("codex:Spark2:本周", {"codex:Spark"})  # 防误前缀


def test_toggle_window_splits_bucket_prefix():
    """桶级排除下打开单个窗口：桶条目拆成兄弟窗口，兄弟保持排除。"""
    keys = ["codex:S:本周", "codex:S:5小时", "codex:M:本周"]
    excludes = {"codex:S"}
    out = toggle_window(excludes, "codex:S:本周", keys)
    assert not key_excluded("codex:S:本周", out)      # 目标已打开
    assert key_excluded("codex:S:5小时", out)          # 兄弟仍排除
    assert not key_excluded("codex:M:本周", out)       # 别的桶不受影响
    # 再切回去 → 精确排除
    out2 = toggle_window(out, "codex:S:本周", keys)
    assert key_excluded("codex:S:本周", out2)
