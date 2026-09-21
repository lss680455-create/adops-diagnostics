# -*- coding: utf-8 -*-
"""命令行入口。

命令刻意做成「一个动作一条命令」，因为这条流水线本来就是给投放日常使用的：
每天跑一次 ``run``，出问题时单跑 ``quality`` / ``diagnose`` / ``sql`` 定位。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import load_config
from .experiment.design import design_experiment, evaluate_existing
from .metrics.ads import (
    cpa_from_cpc,
    cpc_from_cpm,
    cpm_from_cpc,
    ecpm_from_funnel,
    target_cpa_from_ecpm,
)
from .pipeline import run_pipeline


def _print_lines(lines: List[str]) -> None:
    for line in lines:
        print(line)


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。

    公共参数挂在一个 ``add_help=False`` 的父解析器上，再被各子命令继承 ——
    argparse 的经典坑是「父解析器与子解析器共用同一 dest 时，子命令的默认值会
    覆盖父级解析结果」；只挂子命令这一侧可以完全避开它，于是
    ``adops run --out outputs/demo`` 与 ``adops quality --out ...`` 都成立。
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="配置文件路径（默认 configs/default.yaml）")
    common.add_argument("--source", choices=["sample", "csv", "criteo"], help="数据源")
    common.add_argument("--data-file", help="数据文件路径（--source csv / criteo 时使用）")
    common.add_argument("--layout", choices=["auto", "impression", "aggregated"], help="输入布局")
    common.add_argument("--account", help="账户名（出现在报告标题中）")
    common.add_argument("--top-n", type=int, help="报告中展示的计划条数")
    common.add_argument("--out", help="输出目录（默认 outputs）")
    common.add_argument("--no-charts", action="store_true", help="跳过图表渲染")

    parser = argparse.ArgumentParser(
        prog="adops",
        description="广告投放数据诊断流水线：取数 → 清洗 → 体检 → 指标 → 诊断 → 成稿",
    )
    parser.add_argument("--version", action="version", version=f"adops-diagnostics {__version__}")

    sub = parser.add_subparsers(dest="command")

    run_cmd = sub.add_parser("run", parents=[common], help="运行完整流水线并生成报告")
    run_cmd.add_argument("--json", action="store_true", help="额外输出一行 JSON 摘要")

    sub.add_parser("quality", parents=[common], help="跑数据质量体检（含指标与诊断，跳过图表）")
    sub.add_parser("diagnose", parents=[common], help="跑诊断并输出发现清单（跳过图表）")

    sql_cmd = sub.add_parser("sql", parents=[common], help="输出复核用 SQL")
    sql_cmd.add_argument("--name", help="只输出指定片段（计划效率 / 分时效率 / 数据质量自查 / 日趋势 / 归因覆盖率 / 实验读数）")

    design_cmd = sub.add_parser("design", parents=[common], help="实验样本量计算")
    design_cmd.add_argument("--metric", default="ctr", help="主指标（ctr / cvr / ipv / cpa）")
    design_cmd.add_argument("--baseline", type=float, required=True, help="基准值（如 CTR 0.01）")
    design_cmd.add_argument("--daily", type=float, help="每日可获得的样本量")
    design_cmd.add_argument("--mde-rel", type=float, default=0.10, help="期望相对提升（0.10 = 10%%）")
    design_cmd.add_argument("--alpha", type=float, default=0.05, help="显著性水平")
    design_cmd.add_argument("--power", type=float, default=0.80, help="功效")
    design_cmd.add_argument("--looks", type=int, default=1, help="计划中途查看次数")

    eval_cmd = sub.add_parser("evaluate", parents=[common], help="已有实验的读数判定")
    eval_cmd.add_argument("--metric", default="ctr", help="主指标")
    eval_cmd.add_argument("--control", nargs=2, type=float, required=True, metavar=("X1", "N1"),
                          help="对照组：事件数 样本量")
    eval_cmd.add_argument("--variant", nargs=2, type=float, required=True, metavar=("X2", "N2"),
                          help="实验组：事件数 样本量")
    eval_cmd.add_argument("--alpha", type=float, default=0.05, help="显著性水平")

    billing_cmd = sub.add_parser("billing", parents=[common], help="计费口径换算（CPM / CPC / CPA / oCPC）")
    billing_cmd.add_argument("--ctr", type=float, required=True, help="点击率（小数）")
    billing_cmd.add_argument("--cvr", type=float, help="点击转化率（小数）")
    billing_cmd.add_argument("--cpc", type=float, help="单次点击成本")
    billing_cmd.add_argument("--cpm", type=float, help="千次曝光成本")
    billing_cmd.add_argument("--target-cpa", type=float, help="目标转化成本")
    billing_cmd.add_argument("--ecpm", type=float, help="媒体侧千次曝光收入")
    return parser


def _config_from_args(args: argparse.Namespace):
    overrides: Dict[str, Dict[str, Any]] = {}
    run_overrides = {
        key: value
        for key, value in (
            ("source", args.source),
            ("data_file", args.data_file),
            ("layout", args.layout),
            ("account", args.account),
            ("top_n", args.top_n),
            ("out_dir", args.out),
        )
        if value is not None
    }
    if run_overrides:
        overrides["run"] = run_overrides
    return load_config(args.config, **overrides)


def main(argv: Optional[List[str]] = None) -> int:
    """命令行主入口，返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"

    if command == "billing":
        lines: List[str] = []
        if args.cpc is not None:
            lines.append(f"CPM = CPC × CTR × 1000 = {cpm_from_cpc(args.cpc, args.ctr)}")
        if args.cpm is not None:
            lines.append(f"CPC = CPM / (CTR × 1000) = {cpc_from_cpm(args.cpm, args.ctr)}")
        if args.cvr is not None and args.cpc is not None:
            lines.append(f"CPA = CPC / CVR = {cpa_from_cpc(args.cpc, args.cvr)}")
        if args.cvr is not None and args.target_cpa is not None:
            lines.append(
                f"eCPM（媒体侧等价收入） = CTR × CVR × 目标CPA × 1000 = "
                f"{ecpm_from_funnel(args.ctr, args.cvr, args.target_cpa)}"
            )
        if args.ecpm is not None and args.cvr is not None:
            lines.append(
                f"可承受目标CPA = eCPM / (CTR × CVR × 1000) = "
                f"{target_cpa_from_ecpm(args.ecpm, args.ctr, args.cvr)}"
            )
        _print_lines(lines or ["参数不足：至少给出 --ctr 与其中一项已知成本/出价"])
        return 0

    if command == "design":
        plan = design_experiment(
            metric=args.metric,
            baseline_rate=args.baseline,
            daily_denominator=args.daily,
            alpha=args.alpha,
            power=args.power,
            mde_rel=args.mde_rel,
            looks=args.looks,
        )
        print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
        return 0

    if command == "evaluate":
        result = evaluate_existing(
            metric=args.metric,
            x1=args.control[0], n1=args.control[1],
            x2=args.variant[0], n2=args.variant[1],
            alpha=args.alpha,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    config = _config_from_args(args)

    if command == "sql":
        from .compose.sql import all_statements

        statements = all_statements(config.run.top_n)
        if args.name:
            statement = statements.get(args.name)
            if statement is None:
                print(f"未知的 SQL 片段：{args.name}；可选：{'、'.join(statements)}", file=sys.stderr)
                return 2
            print(statement)
            return 0
        for name, statement in statements.items():
            print(f"-- ==== {name} ====")
            print(statement)
            print()
        return 0

    with_charts = not args.no_charts
    if command in {"quality", "diagnose"}:
        with_charts = False  # 这两个子命令用于快速定位问题，跳过渲染省时间
    result = run_pipeline(config, with_charts=with_charts)
    for line in result["logs"]:
        print(f"· {line}")
    print(f"\n报告：{Path(result['output_dir']) / 'report.md'}")
    if getattr(args, "json", False) or command == "run":
        headline = {
            "output_dir": result["output_dir"],
            "latest_period": result.get("latest_period"),
            **result.get("headline", {}),
        }
        print(json.dumps(headline, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
