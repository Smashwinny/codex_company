"""告警阈值（set_thresholds / threshold_color）测试。无需 QApplication，可无头运行。"""

from __future__ import annotations

import pytest

from codex_quota.ui.widgets import (
    COLOR_CRIT,
    COLOR_OK,
    COLOR_UNKNOWN,
    COLOR_WARN,
    set_thresholds,
    threshold_color,
)


@pytest.fixture(autouse=True)
def _restore_defaults():
    yield
    set_thresholds(30, 10)  # 每个测试后恢复默认，防用例间串扰


def test_default_thresholds():
    assert threshold_color(None) == COLOR_UNKNOWN
    assert threshold_color(100) == COLOR_OK
    assert threshold_color(31) == COLOR_OK
    assert threshold_color(30) == COLOR_WARN
    assert threshold_color(11) == COLOR_WARN
    assert threshold_color(10) == COLOR_CRIT
    assert threshold_color(0) == COLOR_CRIT


def test_custom_thresholds_take_effect():
    set_thresholds(50, 20)
    assert threshold_color(45) == COLOR_WARN   # 默认绿，自定义黄
    assert threshold_color(15) == COLOR_CRIT   # 默认黄，自定义红
    assert threshold_color(60) == COLOR_OK


@pytest.mark.parametrize("warn,crit", [
    (10, 30),    # 黄线低于红线
    (30, 30),    # 相等
    (0, 0),
    (-5, 10),
    (101, 50),
    (30, -1),
])
def test_invalid_thresholds_rejected(warn, crit):
    with pytest.raises(ValueError):
        set_thresholds(warn, crit)
    # 拒绝后保持默认
    assert threshold_color(25) == COLOR_WARN
