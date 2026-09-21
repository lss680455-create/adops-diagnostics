# -*- coding: utf-8 -*-
"""命令行测试：参数解析、子命令输出、公共参数位置。"""
from __future__ import annotations

import json

import pytest

from adops.cli import build_parser, main


def test_parser_version_and_subcommands():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--version"])
    assert parser.parse_args(["run"]).command == "run"
    assert parser.parse_args(["sql"]).command == "sql"


def test_common_options_available_after_subcommand():
    """``adops run --out X`` 必须成立：公共参数挂在子命令上。"""
    args = build_parser().parse_args(["run", "--source", "csv", "--out", "outputs/demo",
                                     "--top-n", "3", "--no-charts"])
    assert args.source == "csv"
    assert args.out == "outputs/demo"
    assert args.top_n == 3
    assert args.no_charts is True


def test_billing_command_prints_identity(capsys):
    code = main(["billing", "--ctr", "0.02", "--cpc", "0.5", "--cvr", "0.05",
                 "--target-cpa", "10"])
    out = capsys.readouterr().out
    assert code == 0
    assert "CPM = CPC × CTR × 1000 = 10.0" in out
    assert "CPA = CPC / CVR = 10.0" in out
    assert "eCPM（媒体侧等价收入）" in out


def test_billing_command_without_enough_args(capsys):
    code = main(["billing", "--ctr", "0.02"])
    assert code == 0
    assert "参数不足" in capsys.readouterr().out


def test_design_command_outputs_json(capsys):
    code = main(["design", "--baseline", "0.01", "--daily", "50000", "--mde-rel", "0.2"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["sample_size_per_arm"] > 0
    assert payload["metric"] == "ctr"


def test_evaluate_command_outputs_verdict(capsys):
    code = main(["evaluate", "--control", "100", "10000", "--variant", "150", "10000"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["test"]["significant"] is True
    assert "显著" in payload["verdict"]


def test_sql_command_single_and_all(capsys):
    assert main(["sql", "--name", "计划效率"]) == 0
    single = capsys.readouterr().out
    assert "SELECT" in single and "ad_impressions" in single

    assert main(["sql"]) == 0
    everything = capsys.readouterr().out
    assert "数据质量自查" in everything


def test_sql_command_unknown_name_returns_error(capsys):
    assert main(["sql", "--name", "不存在的片段"]) == 2
    assert "未知的 SQL 片段" in capsys.readouterr().err


def test_run_command_end_to_end_via_cli(raw_csv_factory, tmp_path, capsys):
    export = raw_csv_factory(tmp_path / "export.csv")
    code = main(["run", "--source", "csv", "--data-file", str(export),
                 "--out", str(tmp_path / "cli_out"), "--no-charts"])
    out = capsys.readouterr().out
    assert code == 0
    assert "报告：" in out
    assert (tmp_path / "cli_out" / "report.md").exists()
    assert "\"kpi\"" in out        # 摘要 JSON


def test_quality_subcommand_skips_charts(raw_csv_factory, tmp_path, capsys):
    export = raw_csv_factory(tmp_path / "export.csv")
    code = main(["quality", "--source", "csv", "--data-file", str(export),
                 "--out", str(tmp_path / "q_out")])
    assert code == 0
    capsys.readouterr()
    assert not (tmp_path / "q_out" / "figures").exists()
