# -*- coding: utf-8 -*-
"""列名映射与布局识别的测试。

这一层是整个流水线的入口，投递日志的列名千奇百怪，映射错了后面全错。
"""
from __future__ import annotations

import pytest

from adops.collect.schema import (
    CANONICAL_FIELDS,
    detect_layout,
    map_columns,
    resolve_column,
)


@pytest.mark.parametrize("raw,expected", [
    ("impressions", "impressions"),
    ("IMPRS", "impressions"),
    ("曝光量", "impressions"),
    ("展示", "impressions"),
    ("点击", "clicks"),
    ("clicks", "clicks"),
    ("消耗", "cost"),
    ("stat_cost", "cost"),
    ("花费", "cost"),
    ("转化数", "conversions"),
    ("orders", "conversions"),
    ("campaign_id", "campaign_id"),
    ("计划", "campaign_id"),
    ("广告位", "placement"),
    ("banner_pos", "placement"),
    ("渠道", "channel"),
    ("date", "date"),
    ("小时", "hour"),
    ("uid", "user_id"),
])
def test_resolve_column_aliases(raw, expected):
    assert resolve_column(raw) == expected


def test_resolve_column_unknown_returns_none():
    assert resolve_column("cat17") is None
    assert resolve_column("某个自定义字段") is None


def test_map_columns_reports_unmapped():
    rename, unmapped = map_columns(["date", "计划", "曝光量", "cat1", "我的字段"])
    assert rename["计划"] == "campaign_id"
    assert rename["曝光量"] == "impressions"
    assert set(unmapped) == {"cat1", "我的字段"}


def test_map_columns_first_match_wins():
    """两列映射到同一规范字段时，第一列胜出，第二列记入未识别。"""
    rename, unmapped = map_columns(["cost", "消耗"])
    assert rename == {"cost": "cost"}
    assert unmapped == ["消耗"]


def test_detect_layout_impression():
    assert detect_layout(["timestamp", "uid", "campaign", "click", "cost"]) == "impression"


def test_detect_layout_aggregated():
    assert detect_layout(["日期", "计划", "曝光量", "点击量", "消耗"]) == "aggregated"


def test_detect_layout_prefers_aggregated_when_counts_present():
    """同时出现 click 与 impressions 时按聚合口径判定（数值层会在清洗层复核）。"""
    assert detect_layout(["click", "impressions", "cost"]) == "aggregated"


def test_canonical_fields_cover_metrics_columns():
    for column in ("impressions", "clicks", "conversions", "cost", "campaign_id", "date"):
        assert column in CANONICAL_FIELDS
