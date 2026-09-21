# -*- coding: utf-8 -*-
"""指标纯函数测试：边界、除零、计费口径恒等式。"""
from __future__ import annotations

import math

import pytest

from adops.metrics.ads import (
    INDICATOR_SPECS,
    attributed_rate,
    bid_consistency_gap,
    build_metrics,
    cpa,
    cpa_from_cpc,
    cpc,
    cpc_from_cpm,
    cpm,
    cpm_from_cpc,
    cost_concentration,
    ctr,
    cvr,
    ecpm_from_funnel,
    gini,
    hhi,
    ipv,
    relative_change,
    safe_div,
    target_cpa_from_ecpm,
    top_k_share,
    zscore,
)


# ---------------------------------------------------------------------------
# 基础指标
# ---------------------------------------------------------------------------


def test_ctr_cvr_basic():
    assert ctr(10, 1000) == pytest.approx(0.01)
    assert cvr(5, 100) == pytest.approx(0.05)
    assert ipv(5, 1000) == pytest.approx(0.005)


def test_cost_metrics_basic():
    assert cpc(20.0, 100) == pytest.approx(0.2)
    assert cpm(20.0, 10_000) == pytest.approx(2.0)
    assert cpa(20.0, 4) == pytest.approx(5.0)


@pytest.mark.parametrize("numerator,denominator", [
    (1, 0), (None, 10), (1, None), (float("nan"), 10), (0, 0),
])
def test_safe_div_returns_none_on_invalid(numerator, denominator):
    assert safe_div(numerator, denominator) is None


def test_metrics_return_none_instead_of_raising():
    """零转化 / 零点击不应抛异常，而是返回 None —— 报告要能继续生成。"""
    assert cpa(100.0, 0) is None
    assert cvr(0, 0) is None
    assert cpc(100.0, 0) is None


def test_attributed_rate():
    assert attributed_rate(80, 100) == pytest.approx(0.8)
    assert attributed_rate(0, 0) is None


# ---------------------------------------------------------------------------
# 计费口径换算：恒等式必须成立
# ---------------------------------------------------------------------------


def test_billing_roundtrip_cpc_cpm():
    ctr_value, cpc_value = 0.02, 0.5
    cpm_value = cpm_from_cpc(cpc_value, ctr_value)
    assert cpm_value == pytest.approx(10.0)
    assert cpc_from_cpm(cpm_value, ctr_value) == pytest.approx(cpc_value)


def test_billing_roundtrip_cpa_ecpm():
    ctr_value, cvr_value, target = 0.02, 0.05, 10.0
    ecpm = ecpm_from_funnel(ctr_value, cvr_value, target)
    assert ecpm == pytest.approx(10.0)
    assert target_cpa_from_ecpm(ecpm, ctr_value, cvr_value) == pytest.approx(target)


def test_billing_identity_matches_realised_metrics():
    """``CPM == CPA × CVR × CTR × 1000``：真实数据上恒等（口径自检的依据）。"""
    totals = {"impressions": 100_000, "clicks": 2_000, "conversions": 100, "cost": 300.0}
    metrics = build_metrics(totals)
    derived = metrics["derived"]
    implied = derived["cpa"] * derived["cvr"] * derived["ctr"] * 1000
    assert implied == pytest.approx(derived["cpm"], rel=1e-9)


def test_cpa_from_cpc():
    assert cpa_from_cpc(0.5, 0.05) == pytest.approx(10.0)
    assert cpa_from_cpc(0.5, 0) is None


def test_bid_consistency_gap():
    assert bid_consistency_gap(10.0, 10.0) == pytest.approx(0.0)
    assert bid_consistency_gap(12.0, 10.0) == pytest.approx(0.2)
    assert bid_consistency_gap(10.0, 0) is None


# ---------------------------------------------------------------------------
# 结构指标
# ---------------------------------------------------------------------------


def test_hhi_matches_hand_calculation():
    assert hhi([1, 2, 7]) == pytest.approx(0.54)


def test_top_k_share():
    assert top_k_share([1, 2, 7], 1) == pytest.approx(0.7)
    assert top_k_share([1, 2, 7], 3) == pytest.approx(1.0)
    assert top_k_share([0, 0], 1) is None


def test_gini_matches_hand_calculation():
    assert gini([1, 2, 7]) == pytest.approx(0.4)
    assert gini([5, 5, 5, 5]) == pytest.approx(0.0, abs=1e-12)
    assert gini([0, 0]) is None


def test_concentration_metrics_boundaries():
    values = [1.0, 1.0, 1.0, 1.0]
    assert hhi(values) == pytest.approx(0.25)
    result = cost_concentration({"a": 1.0, "b": 1.0})
    assert result["top1_share"] == pytest.approx(0.5)
    assert result["hhi"] == pytest.approx(0.5)


def test_zscore_and_relative_change():
    assert zscore(10, [1, 2, 3, 4, 5]) > 2
    assert zscore(3, [3, 3, 3]) is None  # 无波动
    assert relative_change(100, 130) == pytest.approx(0.3)
    assert relative_change(0, 130) is None


# ---------------------------------------------------------------------------
# 指标汇总
# ---------------------------------------------------------------------------


def test_build_metrics_structure_and_display():
    totals = {"impressions": 10_000, "clicks": 100, "conversions": 10, "cost": 50.0,
              "attributed_conversions": 8}
    metrics = build_metrics(totals)
    assert metrics["derived"]["ctr"] == pytest.approx(0.01)
    assert metrics["derived"]["cpa"] == pytest.approx(5.0)
    assert metrics["derived"]["attributed_rate"] == pytest.approx(0.8)
    labels = {item["code"]: item["display"] for item in metrics["indicators"]}
    assert labels["ctr"] == "1.00%"
    assert len(metrics["indicators"]) == len(INDICATOR_SPECS)


def test_build_metrics_funnel_step_rates():
    metrics = build_metrics({"impressions": 1000, "clicks": 100, "conversions": 10, "cost": 1.0})
    funnel = metrics["funnel"]
    assert [step["code"] for step in funnel] == ["impressions", "clicks", "conversions"]
    assert funnel[1]["step_display"] == "10.00%"
    assert funnel[2]["step_display"] == "10.00%"
    assert funnel[0]["step_rate"] is None


def test_build_metrics_tolerates_missing_conversions():
    metrics = build_metrics({"impressions": 1000, "clicks": 10, "conversions": 0, "cost": 5.0})
    values = {item["code"]: item["display"] for item in metrics["indicators"]}
    assert values["cpa"] == "—"
    assert values["attributed_rate"] is None or values["attributed_rate"] == "—"
