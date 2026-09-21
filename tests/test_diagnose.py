# -*- coding: utf-8 -*-
"""诊断规则测试：触发条件、样本量门槛、显著性、优先级排序。

重点覆盖两类「不该报」的情形：样本量不足、以及比率差但未达显著——
误报的代价是运营对报表失去信任。
"""
from __future__ import annotations

import pytest

from adops.config import DiagnoseConfig
from adops.diagnose.rules import (
    RULES,
    rule_attribution_gap,
    rule_concentration_risk,
    rule_cpa_trend_worsening,
    rule_cpm_outlier,
    rule_daily_cost_anomaly,
    rule_high_spend_high_cpa,
    rule_high_spend_zero_conversion,
    rule_hourly_opportunity,
    rule_low_ctr,
    rule_low_cvr,
    rule_long_tail_void_spend,
    rule_quality_block,
    run_rules,
)
from adops.diagnose.scoring import LEVEL_WEIGHT, priority_score, rank_findings, summarize

from conftest import make_balanced_frame, make_diagnosis_input, make_frame


# ---------------------------------------------------------------------------
# R01 数据质量阻断
# ---------------------------------------------------------------------------


def test_quality_block_fires_on_failure(balanced_frame):
    ctx = make_diagnosis_input(balanced_frame, quality_failed=True)
    finding = rule_quality_block(ctx)
    assert finding is not None
    assert finding.level == "high"
    assert finding.spend_share == 1.0


def test_quality_block_silent_when_clean(balanced_frame):
    assert rule_quality_block(make_diagnosis_input(balanced_frame)) is None


# ---------------------------------------------------------------------------
# R02 归因
# ---------------------------------------------------------------------------


def test_attribution_gap_fires_below_floor():
    frame = make_frame([{"campaign_id": "A", "impressions": 5000, "clicks": 100,
                         "conversions": 100, "cost": 10.0, "attributed_conversions": 50}])
    finding = rule_attribution_gap(make_diagnosis_input(frame))
    assert finding is not None
    assert "归因" in finding.title


def test_attribution_gap_silent_when_healthy(balanced_frame):
    assert rule_attribution_gap(make_diagnosis_input(balanced_frame)) is None


# ---------------------------------------------------------------------------
# R03 / R04 计划层
# ---------------------------------------------------------------------------


def test_high_spend_high_cpa_fires_for_expensive_campaign():
    """一个计划消耗占比高、CPA 是全局 3 倍，必须被指出。"""
    frame = make_frame([
        {"campaign_id": "BAD", "impressions": 20_000, "clicks": 200, "conversions": 20,
         "cost": 300.0},
        {"campaign_id": "OK1", "impressions": 40_000, "clicks": 400, "conversions": 400,
         "cost": 300.0},
        {"campaign_id": "OK2", "impressions": 40_000, "clicks": 400, "conversions": 400,
         "cost": 300.0},
    ])
    finding = rule_high_spend_high_cpa(make_diagnosis_input(frame))
    assert finding is not None
    assert "BAD" in finding.subject
    assert finding.spend_share > 0


def test_high_spend_high_cpa_respects_sample_size_gate():
    """转化数不到门槛的计划不能被判低效 —— 小样本的 CPA 是噪声。"""
    frame = make_frame([
        {"campaign_id": "TINY", "impressions": 5000, "clicks": 50, "conversions": 1,
         "cost": 200.0},
        {"campaign_id": "OK", "impressions": 60_000, "clicks": 600, "conversions": 600,
         "cost": 400.0},
    ])
    assert rule_high_spend_high_cpa(make_diagnosis_input(frame)) is None


def test_high_spend_zero_conversion_fires():
    frame = make_frame([
        {"campaign_id": "ZERO", "impressions": 20_000, "clicks": 100, "conversions": 0,
         "cost": 200.0},
        {"campaign_id": "OK", "impressions": 60_000, "clicks": 600, "conversions": 600,
         "cost": 600.0},
    ])
    finding = rule_high_spend_zero_conversion(make_diagnosis_input(frame))
    assert finding is not None
    assert finding.level == "high"
    assert "ZERO" in finding.subject


def test_zero_conversion_silent_when_clicks_below_gate():
    frame = make_frame([
        {"campaign_id": "ZERO", "impressions": 20_000, "clicks": 3, "conversions": 0,
         "cost": 200.0},
        {"campaign_id": "OK", "impressions": 60_000, "clicks": 600, "conversions": 600,
         "cost": 600.0},
    ])
    assert rule_high_spend_zero_conversion(make_diagnosis_input(frame)) is None


# ---------------------------------------------------------------------------
# R05 / R06 结构
# ---------------------------------------------------------------------------


def test_concentration_risk_fires_when_single_campaign_dominates():
    rows = [{"campaign_id": "BIG", "impressions": 10_000, "clicks": 100, "conversions": 10,
             "cost": 900.0}]
    rows += [{"campaign_id": f"S{i}", "impressions": 1000, "clicks": 10, "conversions": 1,
              "cost": 50.0} for i in range(9)]
    finding = rule_concentration_risk(make_diagnosis_input(make_frame(rows)))
    assert finding is not None
    assert "集中度" in finding.title


def test_concentration_risk_silent_on_balanced_data(balanced_frame):
    assert rule_concentration_risk(make_diagnosis_input(balanced_frame)) is None


def test_concentration_risk_skipped_when_too_few_campaigns():
    """3 个均分计划的 HHI 恒为 0.333，超过 0.25 阈值但没有信息量 —— 不报。"""
    frame = make_frame([
        {"campaign_id": "A", "impressions": 10_000, "clicks": 100, "conversions": 10,
         "cost": 100.0},
        {"campaign_id": "B", "impressions": 10_000, "clicks": 100, "conversions": 10,
         "cost": 100.0},
        {"campaign_id": "C", "impressions": 10_000, "clicks": 100, "conversions": 10,
         "cost": 100.0},
    ])
    ctx = make_diagnosis_input(frame)
    assert ctx.concentration["hhi"] == pytest.approx(1 / 3, rel=1e-6)
    assert rule_concentration_risk(ctx) is None


def test_long_tail_void_spend_fires():
    rows = [{"campaign_id": "BIG", "impressions": 50_000, "clicks": 500, "conversions": 500,
             "cost": 500.0}]
    rows += [{"campaign_id": f"T{i}", "impressions": 2000, "clicks": 5, "conversions": 0,
              "cost": 8.0} for i in range(10)]
    finding = rule_long_tail_void_spend(make_diagnosis_input(make_frame(rows)))
    assert finding is not None
    assert "长尾" in finding.title


# ---------------------------------------------------------------------------
# R07 / R09 / R10 比率异常（带显著性）
# ---------------------------------------------------------------------------


def test_cpm_outlier_fires_for_expensive_traffic():
    frame = make_frame([
        {"campaign_id": "PRICEY", "impressions": 20_000, "clicks": 200, "conversions": 20,
         "cost": 1000.0},   # CPM 50
        {"campaign_id": "CHEAP", "impressions": 20_000, "clicks": 200, "conversions": 20,
         "cost": 100.0},    # CPM 5
    ])
    finding = rule_cpm_outlier(make_diagnosis_input(frame))
    assert finding is not None
    assert "CPM" in finding.title


def test_cpm_outlier_respects_impression_gate():
    frame = make_frame([
        {"campaign_id": "PRICEY", "impressions": 100, "clicks": 1, "conversions": 0,
         "cost": 50.0},
        {"campaign_id": "CHEAP", "impressions": 100, "clicks": 1, "conversions": 0,
         "cost": 5.0},
    ])
    assert rule_cpm_outlier(make_diagnosis_input(frame)) is None


def test_low_ctr_respects_impression_gate():
    """样本量不到门槛时不应报警：这里把门槛抬到 5000，1100 曝光的计划直接跳过。"""
    strict = DiagnoseConfig(min_impressions=5000)
    frame = make_frame([
        {"campaign_id": "SMALL", "impressions": 1_100, "clicks": 9, "conversions": 0,
         "cost": 5.0},
        {"campaign_id": "BIG", "impressions": 200_000, "clicks": 4000, "conversions": 400,
         "cost": 500.0},
    ])
    assert rule_low_ctr(make_diagnosis_input(frame, cfg=strict)) is None


def test_low_ctr_silent_when_gap_is_small():
    """CTR 低于全局但差距不到 0.5 倍时不算异常（阈值宁高勿低）。"""
    frame = make_frame([
        {"campaign_id": "MILD", "impressions": 300_000, "clicks": 5000, "conversions": 300,
         "cost": 200.0},   # CTR 1.67%，与全局 2% 同量级
        {"campaign_id": "BIG", "impressions": 200_000, "clicks": 4200, "conversions": 400,
         "cost": 200.0},   # CTR 2.1%
    ])
    assert rule_low_ctr(make_diagnosis_input(frame)) is None


def test_low_ctr_fires_when_clearly_worse():
    frame = make_frame([
        {"campaign_id": "BAD", "impressions": 100_000, "clicks": 100, "conversions": 10,
         "cost": 200.0},   # CTR 0.1%
        {"campaign_id": "GOOD", "impressions": 100_000, "clicks": 3000, "conversions": 300,
         "cost": 200.0},   # CTR 3%
    ])
    finding = rule_low_ctr(make_diagnosis_input(frame))
    assert finding is not None
    assert "BAD" in finding.subject
    assert any("p =" in line for line in finding.evidence)


def test_low_cvr_fires_when_landing_page_is_the_problem():
    frame = make_frame([
        {"campaign_id": "WORSE", "impressions": 100_000, "clicks": 2000, "conversions": 2,
         "cost": 200.0},   # CVR 0.1%
        {"campaign_id": "BETTER", "impressions": 100_000, "clicks": 2000, "conversions": 400,
         "cost": 200.0},   # CVR 20%
    ])
    finding = rule_low_cvr(make_diagnosis_input(frame))
    assert finding is not None
    assert "WORSE" in finding.subject


# ---------------------------------------------------------------------------
# R11 时段机会（多重比较校正）
# ---------------------------------------------------------------------------


def test_hourly_opportunity_needs_significant_hour():
    rows = []
    for hour in range(24):
        clicks = 800 if hour == 3 else 100
        rows.append({"date": "2026-09-01", "hour": hour, "campaign_id": "A",
                     "impressions": 20_000, "clicks": clicks, "conversions": 0,
                     "cost": 100.0})
    finding = rule_hourly_opportunity(make_diagnosis_input(make_frame(rows)))
    assert finding is not None
    assert "03:00" in finding.subject
    assert any("Bonferroni" in line for line in finding.evidence)


def test_hourly_opportunity_silent_when_uniform(balanced_frame):
    frame = make_balanced_frame(days=3)
    assert rule_hourly_opportunity(make_diagnosis_input(frame)) is None


# ---------------------------------------------------------------------------
# R12 / R13 趋势与节奏
# ---------------------------------------------------------------------------


def test_cpa_trend_worsening_fires_on_recent_deterioration():
    rows = []
    for day in range(1, 11):
        expensive = day > 7
        rows.append({"date": f"2026-09-{day:02d}", "campaign_id": "A",
                     "impressions": 20_000, "clicks": 200,
                     "conversions": 50 if expensive else 200,
                     "cost": 400.0})
    finding = rule_cpa_trend_worsening(make_diagnosis_input(make_frame(rows)))
    assert finding is not None
    assert "CPA" in finding.title
    assert finding.spend_share == 1.0


def test_cpa_trend_silent_on_stable_data(balanced_frame):
    assert rule_cpa_trend_worsening(make_diagnosis_input(balanced_frame)) is None


def test_cpa_trend_needs_enough_days():
    rows = [{"date": f"2026-09-{d:02d}", "campaign_id": "A", "impressions": 1000,
             "clicks": 10, "conversions": 5, "cost": 100.0} for d in range(1, 5)]
    assert rule_cpa_trend_worsening(make_diagnosis_input(make_frame(rows))) is None


def test_daily_cost_anomaly_fires_on_spike():
    rows = []
    for day in range(1, 16):
        cost = 5000.0 if day == 15 else 100.0
        rows.append({"date": f"2026-09-{day:02d}", "campaign_id": "A", "impressions": 1000,
                     "clicks": 10, "conversions": 1, "cost": cost})
    finding = rule_daily_cost_anomaly(make_diagnosis_input(make_frame(rows)))
    assert finding is not None
    assert "异常" in finding.title


def test_daily_cost_anomaly_silent_on_stable_data(balanced_frame):
    assert rule_daily_cost_anomaly(make_diagnosis_input(balanced_frame)) is None


# ---------------------------------------------------------------------------
# 编排、优先级与容错
# ---------------------------------------------------------------------------


def test_run_rules_executes_all_registered_rules(balanced_frame):
    ctx = make_diagnosis_input(balanced_frame, quality_failed=True)
    findings = run_rules(ctx)
    assert findings  # 至少质量阻断会触发


def test_run_rules_isolates_broken_rule(balanced_frame):
    def broken(_ctx):
        raise RuntimeError("boom")

    ctx = make_diagnosis_input(balanced_frame)
    findings = run_rules(ctx, rules=[broken])
    assert len(findings) == 1
    assert "执行异常" in findings[0].title
    assert "boom" in findings[0].evidence[0]


def test_priority_score_formula():
    assert priority_score("high", 0.0) == pytest.approx(3.0 * 0.3)
    assert priority_score("high", 1.0) == pytest.approx(3.0 * 1.0)
    assert priority_score("low", 0.5) == pytest.approx(1.0 * 0.65)
    assert priority_score("unknown", 0.0) == pytest.approx(0.3)


def test_rank_findings_orders_by_priority_then_level_then_rule():
    from adops.diagnose.rules import Finding

    findings = [
        Finding(rule_id="R09", level="medium", title="m", subject="s", spend_share=0.01),
        Finding(rule_id="R03", level="high", title="h", subject="s", spend_share=0.40),
        Finding(rule_id="R01", level="high", title="h2", subject="s", spend_share=0.05),
    ]
    ranked = rank_findings(findings)
    assert [f.rule_id for f in ranked] == ["R03", "R01", "R09"]
    assert ranked[0].priority >= ranked[-1].priority


def test_summarize_reports_overlap():
    from adops.diagnose.rules import Finding

    findings = [
        Finding(rule_id="R01", level="high", title="a", subject="s", spend_share=0.8),
        Finding(rule_id="R02", level="high", title="b", subject="s", spend_share=0.6),
    ]
    summary = summarize(findings)
    assert summary["total"] == 2
    assert summary["counts"]["high"] == 2
    assert summary["affected_spend_share"] == 1.0
    assert summary["affected_spend_share_raw"] == pytest.approx(1.4)
    assert summary["overlapping"] is True
    assert summary["top"] == "R01"


def test_rule_registry_has_no_duplicate_ids():
    ctx = make_diagnosis_input(make_balanced_frame())
    ids = set()
    for rule in RULES:
        finding = rule(ctx)
        if finding is not None:
            assert finding.rule_id not in ids
            ids.add(finding.rule_id)


def test_diagnose_config_thresholds_are_used_not_hardcoded():
    """把门槛调到不可能满足，规则必须沉默 —— 证明阈值真的来自配置。"""
    strict = DiagnoseConfig(min_conversions=10_000, min_clicks=10_000)
    frame = make_frame([
        {"campaign_id": "BAD", "impressions": 20_000, "clicks": 200, "conversions": 20,
         "cost": 300.0},
        {"campaign_id": "OK", "impressions": 40_000, "clicks": 400, "conversions": 400,
         "cost": 300.0},
    ])
    assert rule_high_spend_high_cpa(make_diagnosis_input(frame, cfg=strict)) is None
    assert rule_high_spend_zero_conversion(make_diagnosis_input(frame, cfg=strict)) is None
    assert rule_low_cvr(make_diagnosis_input(frame, cfg=strict)) is None
