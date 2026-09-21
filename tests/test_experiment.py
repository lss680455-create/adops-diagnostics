# -*- coding: utf-8 -*-
"""统计层测试：已知数值、单调性、序贯边界。

统计实现只用标准库，所以「数值对不对」必须靠已知结果钉住，不能靠感觉。
"""
from __future__ import annotations

import math

import pytest

from adops.experiment.design import design_experiment, evaluate_existing
from adops.experiment.tests_stats import (
    chi_square_2x2,
    mde_for_sample,
    mde_relative,
    norm_cdf,
    norm_ppf,
    obrien_fleming_boundaries,
    power_for_effect,
    sample_size_per_arm,
    sequential_check,
    two_proportion_z_test,
    wilson_interval,
    z_for,
)


def test_norm_cdf_norm_ppf_are_inverses():
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(1.959963985) == pytest.approx(0.975, abs=1e-6)
    assert norm_ppf(0.975) == pytest.approx(1.959963985, abs=1e-6)
    assert norm_ppf(norm_cdf(1.23)) == pytest.approx(1.23, abs=1e-9)


def test_norm_ppf_rejects_out_of_range():
    with pytest.raises(ValueError):
        norm_ppf(0.0)
    with pytest.raises(ValueError):
        norm_ppf(1.0)


def test_z_for_two_sided_and_one_sided():
    assert z_for(0.05) == pytest.approx(1.959963985, abs=1e-6)
    assert z_for(0.05, two_sided=False) == pytest.approx(1.644853627, abs=1e-6)


# ---------------------------------------------------------------------------
# 两比例检验
# ---------------------------------------------------------------------------


def test_two_proportion_z_test_known_values():
    result = two_proportion_z_test(100, 1000, 130, 1000)
    assert result["p1"] == pytest.approx(0.1)
    assert result["p2"] == pytest.approx(0.13)
    assert result["diff"] == pytest.approx(0.03)
    assert 2.10 < result["z"] < 2.11
    assert result["p_value"] < 0.05
    assert result["significant"] is True
    assert result["ci_low"] > 0  # 区间不跨 0


def test_two_proportion_z_test_symmetric_under_swap():
    forward = two_proportion_z_test(100, 1000, 130, 1000)
    backward = two_proportion_z_test(130, 1000, 100, 1000)
    assert forward["p_value"] == pytest.approx(backward["p_value"])
    assert forward["z"] == pytest.approx(-backward["z"])


def test_two_proportion_z_test_not_significant_for_small_diff():
    result = two_proportion_z_test(100, 1000, 105, 1000)
    assert result["significant"] is False
    assert result["ci_low"] < 0 < result["ci_high"]


def test_two_proportion_z_test_handles_zero_samples_and_degenerate_rates():
    assert two_proportion_z_test(0, 0, 1, 10)["note"] == "样本量为 0，无法检验"
    assert "正态近似失效" in two_proportion_z_test(0, 100, 0, 100)["note"]


def test_chi_square_equals_z_squared():
    z_test = two_proportion_z_test(100, 1000, 130, 1000)
    chi = chi_square_2x2(100, 1000, 130, 1000)
    assert chi["chi2"] == pytest.approx(z_test["z"] ** 2)
    assert chi["p_value"] == pytest.approx(z_test["p_value"])


def test_wilson_interval_bounds_and_never_negative():
    interval = wilson_interval(5, 100)
    assert 0.02 < interval["low"] < 0.025
    assert 0.10 < interval["high"] < 0.12
    tiny = wilson_interval(0, 100)
    assert tiny["low"] == 0.0
    assert wilson_interval(0, 0) == {"p": None, "low": None, "high": None}


def test_wilson_interval_shrinks_with_sample_size():
    small = wilson_interval(10, 100)
    large = wilson_interval(1000, 10_000)
    assert (large["high"] - large["low"]) < (small["high"] - small["low"])


# ---------------------------------------------------------------------------
# 样本量 / 功效 / MDE
# ---------------------------------------------------------------------------


def test_sample_size_matches_closed_form():
    n = sample_size_per_arm(0.01, 0.001, alpha=0.05, power=0.80)
    z_alpha, z_beta = z_for(0.05), norm_ppf(0.80)
    expected = math.ceil(
        (z_alpha + z_beta) ** 2 * (0.01 * 0.99 + 0.011 * 0.989) / 0.001 ** 2
    )
    assert n == expected
    assert 150_000 < n < 180_000


def test_sample_size_grows_as_effect_shrinks():
    coarse = sample_size_per_arm(0.01, 0.002)
    fine = sample_size_per_arm(0.01, 0.001)
    assert fine > coarse * 3


def test_sample_size_invalid_inputs_return_none():
    assert sample_size_per_arm(0.0, 0.001) is None
    assert sample_size_per_arm(0.01, 0.0) is None
    assert sample_size_per_arm(0.01, 1.5) is None


def test_power_increases_with_sample_size():
    small = power_for_effect(0.01, 0.001, 50_000)
    large = power_for_effect(0.01, 0.001, 300_000)
    assert small < large
    assert large > 0.95


def test_power_roundtrip_with_required_sample_size():
    n = sample_size_per_arm(0.01, 0.001, alpha=0.05, power=0.80)
    assert power_for_effect(0.01, 0.001, n, alpha=0.05) == pytest.approx(0.80, abs=0.005)


def test_mde_is_inverse_of_power():
    n = 200_000
    mde = mde_for_sample(0.01, n, alpha=0.05, power=0.80)
    assert mde is not None
    assert power_for_effect(0.01, mde, n, alpha=0.05) == pytest.approx(0.80, abs=0.01)
    assert mde_for_sample(0.01, 1, alpha=0.05, power=0.80) is None  # 样本太少，检不到


def test_mde_relative_scales_with_baseline():
    assert mde_relative(0.01, 200_000) > 0
    assert mde_relative(0.0, 200_000) is None


# ---------------------------------------------------------------------------
# 序贯检验
# ---------------------------------------------------------------------------


def test_obrien_fleming_boundaries_control_alpha():
    boundaries = obrien_fleming_boundaries(5, alpha=0.05)
    alphas = [b["alpha"] for b in boundaries]
    assert len(boundaries) == 5
    assert alphas == sorted(alphas)          # 越到后期越宽松
    assert boundaries[0]["alpha"] < 1e-4     # 第一次查看必须极严
    assert boundaries[-1]["alpha"] == pytest.approx(0.05)
    assert boundaries[-1]["z"] == pytest.approx(z_for(0.05))


def test_obrien_fleming_single_look_equals_nominal():
    boundaries = obrien_fleming_boundaries(1, alpha=0.05)
    assert boundaries[0]["alpha"] == pytest.approx(0.05)


def test_obrien_fleming_rejects_invalid_looks():
    with pytest.raises(ValueError):
        obrien_fleming_boundaries(0)


def test_sequential_check_stops_only_at_boundary():
    # 极强效应在第 2 次查看时足以穿过严格边界
    stopped = sequential_check(100, 1000, 200, 1000, look=2, looks=2)
    assert stopped["stopped"] is True
    weak = sequential_check(100, 1000, 102, 1000, look=2, looks=2)
    assert weak["stopped"] is False
    with pytest.raises(ValueError):
        sequential_check(1, 10, 1, 10, look=7, looks=5)


# ---------------------------------------------------------------------------
# 实验方案与读数
# ---------------------------------------------------------------------------


def test_design_experiment_returns_full_plan():
    plan = design_experiment(metric="ctr", baseline_rate=0.01, daily_denominator=50_000,
                             mde_rel=0.10, looks=1)
    assert plan["metric_label"] == "点击率 CTR"
    assert plan["sample_size_per_arm"] > 0
    assert plan["days_required"] >= 7
    assert plan["mde_absolute"] == pytest.approx(0.001)
    assert plan["verdict"]


def test_design_experiment_notes_when_too_long():
    plan = design_experiment(metric="ctr", baseline_rate=0.001, daily_denominator=100,
                             mde_rel=0.05)
    assert plan["days_required"] is not None
    assert any("超过一个月" in note or "无法" in note for note in plan["notes"])


def test_design_experiment_sequential_notes():
    plan = design_experiment(metric="ctr", baseline_rate=0.01, daily_denominator=50_000, looks=5)
    assert any("O'Brien-Fleming" in note for note in plan["notes"])
    assert len(plan["boundaries"]) == 5


def test_evaluate_existing_flags_significant_winner():
    result = evaluate_existing(metric="ctr", x1=100, n1=10_000, x2=150, n2=10_000)
    assert result["verdict"].startswith("实验组显著优于")
    assert "未跨 0" in result["guardrail"]


def test_evaluate_existing_flags_rollback():
    result = evaluate_existing(metric="ctr", x1=150, n1=10_000, x2=100, n2=10_000)
    assert "回滚" in result["verdict"]


def test_evaluate_existing_refuses_to_claim_without_significance():
    result = evaluate_existing(metric="ctr", x1=100, n1=10_000, x2=105, n2=10_000)
    assert "未达显著" in result["verdict"]
    assert "不能按点估直接放量" in result["guardrail"]
