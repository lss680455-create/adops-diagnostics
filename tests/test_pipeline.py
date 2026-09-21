# -*- coding: utf-8 -*-
"""流水线端到端测试：阶段串联、中间产物落盘、确定性、可复算。

不依赖仓库自带夹具，全部在 ``tmp_path`` 里现造数据 —— 测试必须离线且可重复。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from adops.config import load_config
from adops.pipeline import build_pipeline, run_pipeline


@pytest.fixture()
def raw_csv(tmp_path: Path) -> Path:
    """构造一份曝光级日志（列名用别名，覆盖别名映射路径）。"""
    rows = []
    for day in range(1, 11):
        for campaign, base in (("111", 5000), ("222", 4000), ("333", 3000)):
            for index in range(20):
                clicked = 1 if index < 2 else 0
                converted = 1 if (clicked and index == 0) else 0
                rows.append({
                    "日期": f"2026-09-{day:02d}",
                    "小时": index % 24,
                    "广告计划": campaign,
                    "曝光": base // 20,
                    "点击": clicked,
                    "转化": converted,
                    "消耗": 3.0 if campaign == "111" else 2.0,
                })
    frame = pd.DataFrame(rows)
    path = tmp_path / "export.csv"
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _config(raw_csv: Path, out_dir: Path):
    return load_config(None, load_env=False, run={
        "source": "csv",
        "data_file": str(raw_csv),
        "out_dir": str(out_dir),
        "account": "测试账户",
        "top_n": 5,
    })


def test_pipeline_runs_all_stages_and_writes_artifacts(raw_csv, tmp_path):
    out = tmp_path / "outputs"
    result = run_pipeline(_config(raw_csv, out), with_charts=False)
    stages = [stage["stage"] for stage in result["stages"]]
    assert stages == ["collect", "clean", "quality", "metrics", "diagnose",
                      "allocation", "experiment", "compose"]

    for relative in ("clean/ad_events.csv", "quality/checks.json", "quality/checks.md",
                     "metrics/kpi.json", "metrics/campaigns.csv", "metrics/hourly.csv",
                     "metrics/daily.csv", "metrics/allocation.json", "diagnose/findings.json",
                     "experiment/plan.json", "narrative.json", "report.md"):
        assert (out / relative).exists(), relative


def test_pipeline_report_contains_real_numbers(raw_csv, tmp_path):
    out = tmp_path / "outputs"
    run_pipeline(_config(raw_csv, out), with_charts=False)
    report = (out / "report.md").read_text(encoding="utf-8")
    kpi = json.loads((out / "metrics" / "kpi.json").read_text(encoding="utf-8"))

    # 夹具是聚合报表（每行含曝光量）：10 天 × 3 计划 × 每天每计划 5000/4000/3000 曝光
    assert kpi["totals"]["impressions"] == pytest.approx(120_000)
    assert kpi["totals"]["clicks"] == pytest.approx(60)
    assert kpi["totals"]["conversions"] == pytest.approx(30)
    assert "测试账户" in report
    assert "数据质量体检" in report
    # 报告里的 CTR 必须与中间产物一致（可复算性）
    ctr_display = f"{kpi['derived']['ctr'] * 100:.2f}%"
    assert ctr_display in report


def test_pipeline_is_deterministic_modulo_timestamp(raw_csv, tmp_path):
    """两次运行除「生成时间」外必须逐字节一致 —— 否则报告 diff 无意义。"""
    first_out, second_out = tmp_path / "a", tmp_path / "b"
    run_pipeline(_config(raw_csv, first_out), with_charts=False)
    run_pipeline(_config(raw_csv, second_out), with_charts=False)

    strip = lambda text: re.sub(r"- \*\*生成时间\*\*：.*\n", "", text)
    first = strip((first_out / "report.md").read_text(encoding="utf-8"))
    second = strip((second_out / "report.md").read_text(encoding="utf-8"))
    assert first == second

    for relative in ("diagnose/findings.json", "metrics/kpi.json", "metrics/allocation.json"):
        assert (first_out / relative).read_text(encoding="utf-8") == \
               (second_out / relative).read_text(encoding="utf-8")


def test_pipeline_middle_artifacts_allow_stepwise_recompute(raw_csv, tmp_path):
    """从中间产物复算：clean 表的加总必须等于 KPI 里的加总。"""
    out = tmp_path / "outputs"
    run_pipeline(_config(raw_csv, out), with_charts=False)

    cleaned = pd.read_csv(out / "clean" / "ad_events.csv")
    kpi = json.loads((out / "metrics" / "kpi.json").read_text(encoding="utf-8"))
    assert cleaned["impressions"].sum() == pytest.approx(kpi["totals"]["impressions"])
    assert cleaned["clicks"].sum() == pytest.approx(kpi["totals"]["clicks"])
    assert cleaned["cost"].sum() == pytest.approx(kpi["totals"]["cost"], rel=1e-9)


def test_pipeline_with_charts_when_matplotlib_present(raw_csv, tmp_path):
    pytest.importorskip("matplotlib")
    out = tmp_path / "outputs"
    result = run_pipeline(_config(raw_csv, out), with_charts=True)
    figures = [stage for stage in result["stages"] if stage["stage"] == "charts"]
    assert figures and figures[0]["figures"]
    assert (out / "figures").exists()


def test_build_pipeline_shape():
    assert [stage.name for stage in build_pipeline(with_charts=False).stages] == [
        "collect", "clean", "quality", "metrics", "diagnose",
        "allocation", "experiment", "compose",
    ]
    assert "charts" in [stage.name for stage in build_pipeline(with_charts=True).stages]


def test_pipeline_uses_aggregated_layout_when_declared(raw_csv, tmp_path):
    config = load_config(None, load_env=False, run={
        "source": "csv",
        "data_file": str(raw_csv),
        "out_dir": str(tmp_path / "agg"),
        "layout": "aggregated",
    })
    result = run_pipeline(config, with_charts=False)
    clean_stage = next(s for s in result["stages"] if s["stage"] == "clean")
    assert clean_stage["layout"] == "aggregated"
    # 聚合口径下每行是「曝光量」列的值，而不是 1
    assert clean_stage["rows"] == 600


def test_pipeline_reports_missing_data_file(tmp_path):
    config = load_config(None, load_env=False, run={
        "source": "csv",
        "data_file": str(tmp_path / "nope.csv"),
        "out_dir": str(tmp_path / "out"),
    })
    with pytest.raises(Exception):
        run_pipeline(config, with_charts=False)
