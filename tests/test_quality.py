# -*- coding: utf-8 -*-
"""数据质量体检测试：门禁分级、异常检测口径、清洗审计透传。"""
from __future__ import annotations

import pandas as pd
import pytest

from adops.clean.normalize import normalize
from adops.clean.quality import assess_quality, cost_check_basis, render_quality_md
from adops.config import QualityConfig

from conftest import make_frame


def _report(**kwargs):
    from adops.clean.normalize import NormalizeReport

    report = NormalizeReport(raw_rows=kwargs.get("raw_rows", 100), rows=100)
    for key, value in kwargs.items():
        setattr(report, key, value)
    return report


def test_clean_data_passes_all_gates(balanced_frame):
    quality = assess_quality(balanced_frame, _report(), QualityConfig())
    assert quality["failed"] == 0
    assert quality["verdict"].startswith("可用：")


def test_duplicate_rate_gate_fails():
    quality = assess_quality(make_frame([{"campaign_id": "A", "impressions": 10, "clicks": 1,
                                          "conversions": 0, "cost": 1.0}]),
                             _report(raw_rows=100, duplicate_rows=40), QualityConfig())
    checks = {c["check"]: c for c in quality["checks"]}
    assert checks["重复率"]["status"] == "fail"


def test_consistency_gates_flag_conflicts():
    quality = assess_quality(make_frame([{"campaign_id": "A", "impressions": 10, "clicks": 1,
                                          "conversions": 0, "cost": 1.0}]),
                             _report(inconsistent_clicks=5, inconsistent_conversions=2,
                                     negative_cost_rows=1),
                             QualityConfig())
    checks = {c["check"]: c for c in quality["checks"]}
    assert checks["一致性:点击 > 曝光"]["status"] == "fail"
    assert checks["一致性:负消耗"]["status"] == "fail"


def test_attribution_gate_is_warn_not_fail():
    """归因覆盖率偏低是业务发现（交给诊断层 R02），不该把整份报告判成不可用。"""
    frame = make_frame([{"campaign_id": "A", "impressions": 1000, "clicks": 100,
                         "conversions": 100, "cost": 10.0, "attributed_conversions": 50}])
    quality = assess_quality(frame, _report(), QualityConfig())
    checks = {c["check"]: c for c in quality["checks"]}
    assert checks["归因覆盖率"]["status"] == "warn"
    assert quality["failed"] == 0


def test_attribution_gate_fails_when_almost_no_attribution():
    frame = make_frame([{"campaign_id": "A", "impressions": 1000, "clicks": 100,
                         "conversions": 100, "cost": 10.0, "attributed_conversions": 1}])
    quality = assess_quality(frame, _report(), QualityConfig())
    checks = {c["check"]: c for c in quality["checks"]}
    assert checks["归因覆盖率"]["status"] == "fail"


def test_cost_outlier_check_uses_daily_totals_not_raw_rows():
    """消耗异常检测的对象是「按日汇总」，不是单条曝光。

    单次曝光的价格由竞价瞬时决定，天然长尾；拿它当异常检测对象会误报约 7%
    （实测），把正常数据判成异常。这里构造一份逐条看很离散、按日看很平稳的数据，
    断言检测对象是 20 个「天」而不是 4000 行明细。
    """
    rows = []
    for day in range(1, 21):
        for index in range(200):
            cost = day * 0.01 if index else day * 0.01 + 5.0
            rows.append({"date": f"2026-09-{day:02d}", "campaign_id": f"C{day}",
                         "impressions": 1, "clicks": 0, "conversions": 0, "cost": cost})
    frame = make_frame(rows)
    basis, label = cost_check_basis(frame)
    assert label == "按日汇总"
    assert len(basis) == 20

    quality = assess_quality(frame, _report(raw_rows=len(frame)), QualityConfig())
    checks = {c["check"]: c for c in quality["checks"]}
    daily_check = checks["消耗异常值（按日汇总）"]
    assert "/ 20 个" in daily_check["message"]      # 分母是天数，不是明细行数
    assert daily_check["status"] == "ok"


def test_cost_check_basis_falls_back_to_campaign_then_rows():
    daily_frame = make_frame([
        {"date": "2026-09-01", "campaign_id": "A", "impressions": 1, "clicks": 0,
         "conversions": 0, "cost": 1.0},
    ])
    _, label = cost_check_basis(daily_frame)
    assert label == "按明细行"  # 只有 1 天 1 个计划，样本不足

    many_campaigns = make_frame([
        {"date": "2026-09-01", "campaign_id": f"C{i}", "impressions": 1, "clicks": 0,
         "conversions": 0, "cost": float(i)} for i in range(25)
    ])
    _, label = cost_check_basis(many_campaigns)
    assert label == "按计划汇总"


def test_quality_reports_time_coverage_and_layout():
    frame = make_frame([{"date": f"2026-09-{d:02d}", "campaign_id": "A", "impressions": 10,
                         "clicks": 1, "conversions": 0, "cost": 1.0} for d in range(1, 11)])
    quality = assess_quality(frame, _report(distinct_days=10), QualityConfig())
    assert quality["distinct_days"] == 10
    assert quality["rows"] == 10


def test_render_quality_md_contains_verdict_and_rows():
    quality = assess_quality(make_frame([{"campaign_id": "A", "impressions": 10, "clicks": 1,
                                          "conversions": 0, "cost": 1.0}]),
                             _report(), QualityConfig())
    markdown = render_quality_md(quality)
    assert "体检结论" in markdown
    assert "| 检查项 |" in markdown


def test_end_to_end_quality_on_normalized_input(raw_impression_frame):
    frame, report = normalize(raw_impression_frame)
    quality = assess_quality(frame, report, QualityConfig())
    assert quality["layout"] == "impression"
    assert quality["rows"] == 3
