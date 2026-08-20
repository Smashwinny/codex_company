"""托盘取色来源（worst_remaining 排除集合 / color_source_keys）测试。

仅用 duck-typed 视图对象，不实例化 Qt 控件，可无头运行。
"""

from __future__ import annotations

from types import SimpleNamespace

from codex_quota.ui.tray import color_source_keys, worst_remaining
from tests.conftest import codex_snapshot


def _view(name="codex", display="Codex", snap=None):
    return SimpleNamespace(name=name, display_name=display,
                           state=SimpleNamespace(snapshot=snap))


def _snap_with_buckets():
    """codex 真实 fixture：主限额（剩 9%）+ Spark 桶（剩 98%）。"""
    return codex_snapshot()


def test_worst_includes_all_buckets_by_default():
    views = [_view(snap=_snap_with_buckets())]
    assert worst_remaining(views) == 9.0  # 主限额最差


def test_excludes_skip_bucket():
    views = [_view(snap=_snap_with_buckets())]
    spark_key = "codex:GPT-5.3-Codex-Spark"
    keys = [k for k, _ in color_source_keys(views)]
    assert spark_key in keys
    # 排除主限额后只剩 Spark 桶 → 98%
    main_bucket = codex_snapshot().primary_limit
    main_key = f"codex:{main_bucket.limit_name or main_bucket.limit_id}"
    assert worst_remaining(views, frozenset({main_key})) == 98.0
    # 排除 Spark 桶不影响最差值（主限额仍 9%）
    assert worst_remaining(views, frozenset({spark_key})) == 9.0
    # 全部排除 → 无数据
    assert worst_remaining(views, frozenset(keys)) is None


def test_excludes_are_scoped_per_provider():
    views = [_view(snap=_snap_with_buckets()),
             _view(name="kimi", display="Kimi", snap=None)]
    # 同名桶挂在别的 provider 上不受影响；无快照 provider 本就不参与
    assert color_source_keys(views) == [
        (k, l) for k, l in color_source_keys(views) if k.startswith("codex:")]
