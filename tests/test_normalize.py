# -*- coding: utf-8 -*-
"""清洗层测试：布局识别、时间口径、去重、一致性钳位。"""
from __future__ import annotations

import pandas as pd
import pytest

from adops.clean.normalize import latest_period, normalize, resolve_layout, write_tidy


def test_normalize_impression_layout(raw_impression_frame):
    frame, report = normalize(raw_impression_frame)
    assert report.layout == "impression"
    assert report.rows == 3
    assert frame["impressions"].sum() == 3
    assert frame["clicks"].sum() == 2
    assert frame["conversions"].sum() == 1
    assert frame["attributed_conversions"].sum() == 1
    assert set(frame["campaign_id"]) == {111, 222}


def test_normalize_derives_relative_day_and_hour(raw_impression_frame):
    """相对秒数时间戳必须还原成「第 N 天 + 小时」，且不能伪造日历日期。"""
    frame, report = normalize(raw_impression_frame)
    assert report.time_basis == "relative_day"
    assert set(frame["date"]) == {"D001"}
    assert sorted(set(frame["hour"])) == [0, 1]


def test_normalize_absolute_timestamp_becomes_calendar_date():
    frame = pd.DataFrame([
        {"timestamp": 1_700_000_000, "campaign": "A", "click": 1, "cost": 1.0},
        {"timestamp": 1_700_003_600, "campaign": "A", "click": 0, "cost": 1.0},
    ])
    cleaned, report = normalize(frame)
    assert report.time_basis == "absolute"
    assert cleaned["date"].iloc[0].startswith("2023-11")


def test_normalize_aggregated_layout_keeps_columns():
    frame = pd.DataFrame([
        {"日期": "2026-09-01", "计划": "A", "曝光量": 1000, "点击量": 10,
         "转化数": 2, "消耗": 12.5},
    ])
    cleaned, report = normalize(frame)
    assert report.layout == "aggregated"
    assert cleaned["impressions"].iloc[0] == 1000
    assert cleaned["campaign_id"].iloc[0] == "A"


def test_resolve_layout_declared_wins():
    frame = pd.DataFrame([{"impressions": 5, "clicks": 1}])
    assert resolve_layout(frame, "impression") == "impression"
    assert resolve_layout(frame, "aggregated") == "aggregated"
    with pytest.raises(ValueError):
        resolve_layout(frame, "bogus")


def test_dedupe_keeps_rows_differing_only_by_user_id():
    """两条曝光只有 uid 不同 —— 是两次真实曝光，不能当重复数据删掉。

    这是一个真实的坑：早期版本去重时排除了 user_id，导致 2.76% 的假重复率，
    把数据判成「不可用」。
    """
    frame = pd.DataFrame([
        {"timestamp": 0, "uid": 1, "campaign": "A", "click": 0, "cost": 1e-05},
        {"timestamp": 0, "uid": 2, "campaign": "A", "click": 0, "cost": 1e-05},
    ])
    cleaned, report = normalize(frame)
    assert report.duplicate_rows == 0
    assert len(cleaned) == 2


def test_dedupe_removes_true_duplicates():
    row = {"timestamp": 0, "uid": 1, "campaign": "A", "click": 0, "cost": 1e-05}
    frame = pd.DataFrame([row, dict(row)])
    cleaned, report = normalize(frame)
    assert report.duplicate_rows == 1
    assert len(cleaned) == 1


def test_clicks_are_clamped_to_impressions_and_recorded():
    """点击 > 曝光是数据冲突：钳位保证指标不炸，同时必须记录下来。"""
    frame = pd.DataFrame([{"日期": "2026-09-01", "计划": "A", "曝光量": 10, "点击量": 99,
                           "转化数": 99, "消耗": 1.0}])
    cleaned, report = normalize(frame)
    assert report.inconsistent_clicks == 1
    assert report.inconsistent_conversions == 1
    assert cleaned["clicks"].iloc[0] == 10
    assert cleaned["conversions"].iloc[0] == 10


def test_negative_cost_is_flagged_and_nulled():
    frame = pd.DataFrame([{"日期": "2026-09-01", "计划": "A", "曝光量": 10, "点击量": 1,
                           "消耗": -5.0}])
    cleaned, report = normalize(frame)
    assert report.negative_cost_rows == 1
    assert cleaned["cost"].iloc[0] == 0


def test_normalize_rejects_empty_and_missing_required():
    with pytest.raises(ValueError):
        normalize(pd.DataFrame())
    with pytest.raises(ValueError):
        normalize(pd.DataFrame([{"无关列": 1}]))


def test_write_tidy_and_latest_period(tmp_path):
    frame = pd.DataFrame([{"date": "2026-09-01", "impressions": 1.0, "clicks": 1.0,
                           "conversions": 0.0, "cost": 0.0}])
    path = write_tidy(frame, tmp_path / "nested" / "tidy.csv")
    assert path.exists()
    assert path.read_text(encoding="utf-8-sig").startswith("date")
    assert latest_period(frame) == "2026-09-01"
