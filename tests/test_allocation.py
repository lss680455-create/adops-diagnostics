# -*- coding: utf-8 -*-
"""预算再分配模拟测试：确定性、约束与假设的可见性。"""
from __future__ import annotations

import pytest

from adops.metrics.allocation import simulate_reallocation
from adops.metrics.ads import reallocate_budget


def _campaigns():
    """一个效率差的高消耗计划 + 两个效率好的计划。"""
    return [
        {"campaign_id": "BAD", "cost": 60.0, "conversions": 6.0},    # CPA 10
        {"campaign_id": "GOOD1", "cost": 20.0, "conversions": 10.0},  # CPA 2
        {"campaign_id": "GOOD2", "cost": 20.0, "conversions": 8.0},   # CPA 2.5
    ]


def test_simulate_reallocation_moves_budget_from_inefficient():
    """只有 CPA 严格低于中位数的计划才承接预算，等于中位的维持不动。"""
    result = simulate_reallocation(_campaigns())
    moves = {move["campaign_id"]: move for move in result["moves"]}
    assert moves["BAD"]["delta"] < 0        # CPA 10，高于中位 2.5 的 (1+tolerance) 倍
    assert moves["GOOD1"]["delta"] > 0      # CPA 2，低于中位 → 承接
    assert "GOOD2" not in moves             # CPA 2.5 == 中位 → 维持不变


def test_simulate_reallocation_is_budget_neutral():
    result = simulate_reallocation(_campaigns())
    assert sum(move["delta"] for move in result["moves"]) == pytest.approx(0.0, abs=1e-9)
    assert result["moved_cost"] > 0


def test_simulate_reallocation_improves_projected_cpa():
    result = simulate_reallocation(_campaigns())
    assert result["cpa_after"] < result["cpa_before"]
    assert result["improvement"] > 0


def test_projection_uses_current_cpa_on_projected_cost():
    """逐项复算一阶近似：只有按当前 CPA 线性缩放才算得出这个 cpa_after。"""
    campaigns = _campaigns()
    result = simulate_reallocation(campaigns)
    total_cost = sum(c["cost"] for c in campaigns)
    projected_conversions = 0.0
    for campaign in campaigns:
        delta = result_moves_delta(result, campaign["campaign_id"])
        projected_conversions += (campaign["cost"] + delta) / (campaign["cost"] / campaign["conversions"])
    assert result["cpa_after"] == pytest.approx(total_cost / projected_conversions)


def result_moves_delta(result, campaign_id: str) -> float:
    for move in result["moves"]:
        if move["campaign_id"] == campaign_id:
            return move["delta"]
    return 0.0


def test_simulate_reallocation_is_deterministic():
    first = simulate_reallocation(_campaigns())
    second = simulate_reallocation(_campaigns())
    assert first == second


def test_simulate_reallocation_needs_enough_campaigns():
    result = simulate_reallocation(_campaigns()[:2])
    assert result["moves"] == []
    assert "样本不足" in result["note"] or "少于" in result["note"]


def test_simulate_reallocation_no_move_when_all_equal():
    campaigns = [
        {"campaign_id": "A", "cost": 10.0, "conversions": 5.0},
        {"campaign_id": "B", "cost": 10.0, "conversions": 5.0},
        {"campaign_id": "C", "cost": 10.0, "conversions": 5.0},
    ]
    result = simulate_reallocation(campaigns)
    assert result["moves"] == []
    assert result["improvement"] == 0.0


def test_max_cut_ratio_is_respected():
    campaigns = [
        {"campaign_id": "BAD", "cost": 100.0, "conversions": 1.0},   # CPA 100
        {"campaign_id": "GOOD", "cost": 10.0, "conversions": 100.0},  # CPA 0.1
        {"campaign_id": "MID", "cost": 10.0, "conversions": 20.0},    # CPA 0.5
    ]
    result = simulate_reallocation(campaigns, max_cut_ratio=0.5)
    bad_move = next(m for m in result["moves"] if m["campaign_id"] == "BAD")
    assert bad_move["delta"] >= -50.0


def test_assumption_is_always_reported():
    """一阶近似的假设必须随结果一起返回 —— 否则会被当成承诺。"""
    for result in (simulate_reallocation(_campaigns()), simulate_reallocation(_campaigns()[:2])):
        assert "线性缩放" in result["assumption"]


def test_reallocate_budget_marks_reasons():
    rows = reallocate_budget(_campaigns(), tolerance=0.2)
    reasons = {row["campaign_id"]: row["reason"] for row in rows}
    assert "高于中位" in reasons["BAD"]
    assert "低于中位" in reasons["GOOD1"]
