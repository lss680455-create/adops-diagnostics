# -*- coding: utf-8 -*-
"""聚合层测试：口径纪律「先加总再相除」必须被锁死。

这是整份报告最容易被质疑的地方——如果对行级比率求平均，小样本行会被同等
加权，CTR / CPA 全错，而报表看起来毫无异常。
"""
from __future__ import annotations

import pandas as pd
import pytest

from adops.metrics.aggregate import (
    aggregate,
    campaign_table,
    daily_totals,
    dimension_tables,
    hourly_totals,
    overall_totals,
)

from conftest import make_frame


def test_ratio_is_sum_then_divide_not_mean_of_ratios():
    """两行数据：一行小样本高 CTR、一行大样本低 CTR。

    正确的整体 CTR 是 15/1010 ≈ 1.49%，而「行级 CTR 求平均」会得到 25.5%。
    """
    frame = make_frame([
        {"campaign_id": "A", "impressions": 10, "clicks": 5, "conversions": 1, "cost": 1.0},
        {"campaign_id": "B", "impressions": 1000, "clicks": 10, "conversions": 1, "cost": 10.0},
    ])
    totals = overall_totals(frame)
    table = campaign_table(frame, top_n=10)
    overall_ctr = totals["clicks"] / totals["impressions"]
    assert overall_ctr == pytest.approx(15 / 1010)
    naive_mean = table["ctr"].mean()
    assert overall_ctr != pytest.approx(naive_mean)
    assert naive_mean > overall_ctr  # 朴素平均把 CTR 抬高了 17 倍


def test_aggregate_derived_columns_consistent():
    frame = make_frame([
        {"campaign_id": "A", "impressions": 1000, "clicks": 20, "conversions": 2, "cost": 30.0},
        {"campaign_id": "A", "impressions": 1000, "clicks": 10, "conversions": 1, "cost": 15.0},
    ])
    grouped = aggregate(frame, ["campaign_id"])
    row = grouped.iloc[0]
    assert row["impressions"] == 2000
    assert row["clicks"] == 30
    assert row["ctr"] == pytest.approx(30 / 2000)
    assert row["cpc"] == pytest.approx(45.0 / 30)
    assert row["cpm"] == pytest.approx(45.0 / 2000 * 1000)
    assert row["cpa"] == pytest.approx(45.0 / 3)
    assert row["spend_share"] == pytest.approx(1.0)


def test_aggregate_sorted_by_cost_desc_and_share_sums_to_one():
    frame = make_frame([
        {"campaign_id": "A", "impressions": 100, "clicks": 1, "conversions": 0, "cost": 1.0},
        {"campaign_id": "B", "impressions": 100, "clicks": 1, "conversions": 0, "cost": 9.0},
    ])
    grouped = aggregate(frame, ["campaign_id"])
    assert list(grouped["campaign_id"]) == ["B", "A"]
    assert grouped["spend_share"].sum() == pytest.approx(1.0)


def test_aggregate_unknown_dimension_raises():
    frame = make_frame([{"campaign_id": "A", "impressions": 1, "clicks": 1, "conversions": 0,
                         "cost": 1.0}])
    with pytest.raises(ValueError):
        aggregate(frame, ["不存在的维度"])


def test_daily_and_hourly_totals_sorted():
    frame = make_frame([
        {"date": "2026-09-02", "hour": 9, "campaign_id": "A", "impressions": 10, "clicks": 1,
         "conversions": 0, "cost": 1.0},
        {"date": "2026-09-01", "hour": 20, "campaign_id": "A", "impressions": 10, "clicks": 1,
         "conversions": 0, "cost": 1.0},
    ])
    daily = daily_totals(frame)
    assert list(daily["date"]) == ["2026-09-01", "2026-09-02"]
    hourly = hourly_totals(frame)
    assert list(hourly["hour"]) == [9, 20]


def test_dimension_tables_only_include_present_dimensions():
    frame = make_frame([{"campaign_id": "A", "impressions": 1, "clicks": 1, "conversions": 0,
                         "cost": 1.0}])
    tables = dimension_tables(frame)
    assert "campaign_id" in tables
    assert "placement" not in tables


def test_campaign_table_limits_rows():
    rows = [{"campaign_id": f"C{i}", "impressions": 100, "clicks": 1, "conversions": 0,
             "cost": float(i)} for i in range(1, 21)]
    table = campaign_table(make_frame(rows), top_n=5)
    assert len(table) == 5
    assert table["cost"].iloc[0] == 20.0


def test_overall_totals_includes_attributed():
    frame = make_frame([{"campaign_id": "A", "impressions": 10, "clicks": 2, "conversions": 1,
                         "cost": 3.0, "attributed_conversions": 0}])
    totals = overall_totals(frame)
    assert totals["attributed_conversions"] == 0
    assert totals["rows"] == 1
