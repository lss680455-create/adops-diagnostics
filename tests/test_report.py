# -*- coding: utf-8 -*-
"""成稿层测试：报告结构、口径附录、SQL 生成、计划判定分层。"""
from __future__ import annotations

import pandas as pd
import pytest

from adops.compose.report import build_report, campaign_verdicts, write_report
from adops.compose.sql import all_statements, render_sql_appendix
from adops.diagnose.scoring import rank_findings, summarize
from adops.experiment.design import design_experiment
from adops.metrics.aggregate import campaign_table, daily_totals, hourly_totals

from conftest import make_diagnosis_input, make_frame


def _build(tmp_path):
    frame = make_frame([
        {"date": "2026-09-01", "campaign_id": "A", "impressions": 10_000, "clicks": 100,
         "conversions": 10, "cost": 50.0},
        {"date": "2026-09-02", "campaign_id": "B", "impressions": 10_000, "clicks": 50,
         "conversions": 0, "cost": 80.0},
    ])
    ctx = make_diagnosis_input(frame)
    findings = rank_findings([])
    narrative = {"text": "测试叙述。", "mode": "template", "guard": {"checked": 0, "unbound": []}}
    return build_report(
        account="测试账户",
        metrics=ctx.metrics,
        quality=ctx.quality,
        concentration=ctx.concentration,
        findings=findings,
        summary=summarize(findings),
        campaign_table=ctx.campaign_table,
        hourly=ctx.hourly,
        daily=ctx.daily,
        allocation={"moves": [], "note": "样本不足", "assumption": "模型假设：线性缩放"},
        experiment_plan=design_experiment(metric="ctr", baseline_rate=0.01, daily_denominator=10_000),
        narrative=narrative,
        normalize_report=None,
        charts={"funnel": tmp_path / "funnel.png"},
        top_n=5,
        data_source="test.csv",
        sql_appendix=render_sql_appendix(5),
        data_boundary="- 测试数据边界声明",
    )


def test_report_contains_all_sections(tmp_path):
    report = _build(tmp_path)
    for heading in ("## 0. 结论摘要", "## 1. 核心指标", "## 2. 数据质量体检", "## 3. 计划效率",
                    "## 5. 诊断发现", "## 6. 预算再分配建议", "## 7. 下一轮实验设计",
                    "## 9. 附录"):
        assert heading in report


def test_report_includes_metric_definitions_and_sql(tmp_path):
    report = _build(tmp_path)
    assert "### 指标口径" in report
    assert "### 复核用 SQL" in report
    assert "NULLIF" in report
    assert "### 数据边界与口径说明" in report


def test_report_billing_identity_check_is_self_consistent(tmp_path):
    """CPM 恒等式自检必须显示为「口径自洽」。"""
    report = _build(tmp_path)
    assert "口径自洽" in report


def test_report_writes_to_disk(tmp_path):
    path = write_report(_build(tmp_path), tmp_path / "nested" / "report.md")
    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("# 广告投放数据诊断报告")


def test_campaign_verdicts_order_of_judgement():
    """判定顺序：先零转化，再样本不足，最后才比 CPA。"""
    table = pd.DataFrame([
        {"campaign_id": "ZERO", "clicks": 100, "conversions": 0, "cpa": None},
        {"campaign_id": "LOW_SAMPLE", "clicks": 100, "conversions": 2, "cpa": 5.0},
        {"campaign_id": "POOR", "clicks": 100, "conversions": 50, "cpa": 20.0},
        {"campaign_id": "OK", "clicks": 100, "conversions": 50, "cpa": 6.0},
        {"campaign_id": "EXCELLENT", "clicks": 100, "conversions": 50, "cpa": 2.0},
    ])
    verdicts = campaign_verdicts(table, global_cpa=8.0, min_conversions=10, min_clicks=30,
                                 poor_multiplier=1.5)
    assert verdicts == ["零转化", "样本不足", "效率低", "持平", "效率优"]


def test_campaign_verdicts_marks_small_sample_zero_conversion():
    table = pd.DataFrame([{"campaign_id": "TINY", "clicks": 3, "conversions": 0, "cpa": None}])
    assert campaign_verdicts(table, global_cpa=5.0) == ["样本不足"]


def test_sql_statements_follow_aggregate_then_divide():
    statements = all_statements(10)
    assert set(statements) == {"计划效率", "分时效率", "数据质量自查", "日趋势", "归因覆盖率", "实验读数"}
    for name, statement in statements.items():
        assert statement.strip().upper().startswith("SELECT"), name
        assert "ad_impressions" in statement
    # 比率必须靠 SUM 相除，不能出现 AVG(比率)
    assert "AVG(" not in statements["计划效率"].upper()
    assert "NULLIF" in statements["计划效率"]


def test_sql_appendix_renders_code_blocks():
    appendix = render_sql_appendix(3)
    assert appendix.count("```sql") == 6
    assert "表名约定" in appendix
