# -*- coding: utf-8 -*-
"""流水线：把「取数 → 清洗 → 体检 → 指标 → 诊断 → 图表 → 调仓 → 实验 → 成稿」
九个阶段显式化并串联。

每个阶段实现 :class:`Stage` 协议、单独可测，输入输出通过
:class:`PipelineContext` 传递；中间产物全部落盘到 ``outputs/``，便于
「报告里的数字逐层复算」——这一点是整条流水线的设计前提，不是附加功能。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

import pandas as pd

from .ai.narrator import build_narrative_payload, narrate
from .clean.normalize import latest_period, normalize, write_tidy
from .clean.quality import assess_quality, render_quality_md
from .collect.impressions import AdDataSource, CsvSource, DataSourceError, SampleSource
from .compose.report import build_report, write_report
from .compose.sql import render_sql_appendix
from .config import AppConfig
from .diagnose.rules import DiagnosisInput, run_rules
from .diagnose.scoring import rank_findings, summarize
from .experiment.design import design_experiment
from .metrics.ads import build_metrics, cost_concentration
from .metrics.aggregate import (
    campaign_table as build_campaign_table,
    daily_totals,
    hourly_totals,
    overall_totals,
)
from .metrics.allocation import simulate_reallocation

#: 使用公开数据集时的数据边界声明。
#:
#: 这段必须印在报告里：公开数据集经过抽样与脱敏，绝对水平（CTR / CPM / CPA）
#: 不代表任何行业基准；消耗字段是「变换后的价格」，不是真实货币金额。
#: 报告里如果不写这句，读者会把 37% 的 CTR 当成真实投放水平——那是最危险的
#: 一类错误：数字算对了，但含义被误读。
PUBLIC_DATASET_BOUNDARY = """- **数据来源**：Criteo Attribution Modeling for Bidding Dataset（公开研究数据集，
  CC BY-NC-SA 4.0，非商用），由 `scripts/build_fixture.py` 抽样/聚合生成离线夹具。
- **绝对水平不可当基准**：该数据集经过抽样与脱敏，CTR / CPM / CPA 的绝对数值不代表
  任何行业的真实水平；本报告可用于验证口径、流程与诊断逻辑，不能用于横向对比外部账户。
- **消耗单位非货币**：源数据的 `cost` 字段是「变换后的价格」（数据集作者的明确说明），
  因此报告中的金额一律是**口径单位**，不是元；比较只能用相对关系（倍数、占比）。
- **时间口径**：源数据的时间戳是「距首条曝光经过的秒数」，报告中的 `D001` 是相对天序号，
  不是日历日期。
- **换成自己的数据**：把投放后台导出接到 `--source csv --data-file <导出文件>`，
  所有口径与阈值不变，结论立刻可用于决策。"""


@dataclass
class PipelineContext:
    """阶段间共享的运行时上下文。"""

    config: AppConfig
    output_dir: Path
    data: Dict[str, Any] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        self.logs.append(message)

    def path(self, *parts: str) -> Path:
        return self.output_dir.joinpath(*parts)

    def dump_json(self, relative: str, payload: Any) -> Path:
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
        return path


class Stage(Protocol):
    """阶段协议。"""

    name: str

    def run(self, ctx: PipelineContext) -> Dict[str, Any]:  # pragma: no cover - 协议
        ...


# ---------------------------------------------------------------------------
# 标准阶段
# ---------------------------------------------------------------------------


class CollectStage:
    """取数：按配置选择数据源，原始表落盘。"""

    name = "collect"

    def __init__(self, source: Optional[AdDataSource] = None):
        self._source = source

    def _build_source(self, cfg: AppConfig) -> AdDataSource:
        if self._source is not None:
            return self._source
        run = cfg.run
        if run.source == "sample":
            return SampleSource(cfg.resolve(run.data_file))
        if run.source == "csv":
            return CsvSource(cfg.resolve(run.data_file))
        if run.source == "criteo":
            return CsvSource(cfg.resolve(run.data_file), label="criteo")
        raise DataSourceError(f"未知数据源：{run.source}（仅支持 sample / csv / criteo）")

    def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        source = self._build_source(ctx.config)
        frame = source.load()
        ctx.data["raw_frame"] = frame
        ctx.data["source_meta"] = source.meta() if hasattr(source, "meta") else {}
        ctx.log(f"取数完成：{len(frame)} 行，来源 {ctx.data['source_meta'].get('source', '?')}")
        return {"rows": int(len(frame)), "source": ctx.data["source_meta"].get("source")}


class CleanStage:
    """清洗：统一计数表落盘到 ``clean/ad_events.csv``。"""

    name = "clean"

    def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        raw = ctx.data.get("raw_frame")
        if raw is None:
            raise RuntimeError("clean 阶段缺少 raw_frame：请先运行 collect 阶段")
        frame, report = normalize(raw, layout=ctx.config.run.layout)
        ctx.data["frame"] = frame
        ctx.data["normalize_report"] = report
        path = write_tidy(frame, ctx.path("clean", "ad_events.csv"))
        ctx.log(
            f"清洗完成：{report.raw_rows} → {report.rows} 行，布局 {report.layout}，"
            f"时间口径 {report.time_basis}，重复 {report.duplicate_rows} 行"
        )
        return {
            "rows": report.rows,
            "layout": report.layout,
            "duplicates": report.duplicate_rows,
            "file": str(path),
        }


class QualityStage:
    """体检：数据质量检查项落盘。"""

    name = "quality"

    def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        frame = ctx.data.get("frame")
        report = ctx.data.get("normalize_report")
        if frame is None:
            raise RuntimeError("quality 阶段缺少 frame：请先运行 clean 阶段")
        quality = assess_quality(frame, report, ctx.config.quality)
        ctx.data["quality"] = quality
        ctx.dump_json("quality/checks.json", quality)
        ctx.path("quality", "checks.md").write_text(render_quality_md(quality), encoding="utf-8")
        ctx.log(
            f"体检完成：{len(quality['checks'])} 项，失败 {quality['failed']}、"
            f"警告 {quality['warned']}；{quality['verdict']}"
        )
        return {"failed": quality["failed"], "warned": quality["warned"], "verdict": quality["verdict"]}


class MetricsStage:
    """指标：KPI、维度表、集中度落盘。"""

    name = "metrics"

    def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        frame = ctx.data.get("frame")
        if frame is None:
            raise RuntimeError("metrics 阶段缺少 frame：请先运行 clean 阶段")
        top_n = ctx.config.run.top_n

        totals = overall_totals(frame)
        metrics = build_metrics(totals)
        campaigns = build_campaign_table(frame, top_n=top_n * 3 if top_n else 30)
        hourly = hourly_totals(frame)
        daily = daily_totals(frame)
        concentration = cost_concentration(
            {str(row["campaign_id"]): row["cost"] for row in campaigns.to_dict(orient="records")}
        )

        ctx.data.update({
            "totals": totals,
            "metrics": metrics,
            "campaign_table": campaigns,
            "hourly": hourly,
            "daily": daily,
            "concentration": concentration,
        })
        ctx.dump_json("metrics/kpi.json", {
            "totals": totals,
            "derived": metrics["derived"],
            "indicators": metrics["indicators"],
            "funnel": metrics["funnel"],
            "concentration": concentration,
        })
        campaigns.to_csv(ctx.path("metrics", "campaigns.csv"), index=False, encoding="utf-8-sig")
        hourly.to_csv(ctx.path("metrics", "hourly.csv"), index=False, encoding="utf-8-sig")
        daily.to_csv(ctx.path("metrics", "daily.csv"), index=False, encoding="utf-8-sig")
        ctx.log(
            f"指标完成：CTR {metrics['derived']['ctr'] if metrics['derived']['ctr'] is None else round(metrics['derived']['ctr'], 6)}、"
            f"CPA {metrics['derived']['cpa'] if metrics['derived']['cpa'] is None else round(metrics['derived']['cpa'], 6)}、"
            f"计划 {len(campaigns)} 个、覆盖 {len(daily)} 天"
        )
        return {"campaigns": int(len(campaigns)), "days": int(len(daily))}


class DiagnoseStage:
    """诊断：规则引擎产出发现并排序。"""

    name = "diagnose"

    def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        metrics = ctx.data.get("metrics")
        if metrics is None:
            raise RuntimeError("diagnose 阶段缺少 metrics：请先运行 metrics 阶段")

        rule_input = DiagnosisInput(
            cfg=ctx.config.diagnose,
            totals=ctx.data["totals"],
            metrics=metrics,
            campaign_table=ctx.data["campaign_table"],
            hourly=ctx.data["hourly"],
            daily=ctx.data["daily"],
            quality=ctx.data["quality"],
            concentration=ctx.data["concentration"],
        )
        findings = rank_findings(run_rules(rule_input))
        summary = summarize(findings)
        ctx.data["findings"] = findings
        ctx.data["summary"] = summary
        ctx.dump_json("diagnose/findings.json", {
            "summary": summary,
            "findings": [f.to_dict() for f in findings],
        })
        ctx.log(
            f"诊断完成：{summary['total']} 项（高 {summary['counts']['high']}、"
            f"中 {summary['counts']['medium']}、低 {summary['counts']['low']}）"
        )
        return {"findings": summary["total"], "counts": summary["counts"]}


class ChartsStage:
    """图表：渲染到 ``figures/``；单张失败不影响流水线。"""

    name = "charts"

    def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        from .charts.report_charts import render_all  # 延迟导入，避免无头环境初始化 matplotlib

        metrics = ctx.data.get("metrics")
        if metrics is None:
            raise RuntimeError("charts 阶段缺少 metrics：请先运行 metrics 阶段")
        paths = render_all(
            metrics,
            ctx.data["campaign_table"],
            ctx.data["hourly"],
            ctx.data["daily"],
            ctx.data["concentration"],
            ctx.path("figures"),
            cfg=ctx.config.charts,
            top_n=ctx.config.run.top_n,
        )
        ctx.data["charts"] = paths
        ctx.log(f"图表渲染完成：{len(paths)} 张")
        return {"figures": [Path(p).name for p in paths.values()]}


class AllocationStage:
    """调仓模拟：确定性算法给出再分配建议。"""

    name = "allocation"

    def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        campaigns = ctx.data.get("campaign_table")
        if campaigns is None:
            raise RuntimeError("allocation 阶段缺少 campaign_table：请先运行 metrics 阶段")
        allocation = simulate_reallocation(
            campaigns.to_dict(orient="records"),
            tolerance=1.0,
            min_conversions=ctx.config.diagnose.min_conversions,
        )
        ctx.data["allocation"] = allocation
        ctx.dump_json("metrics/allocation.json", allocation)
        ctx.log(
            "调仓建议：" + (
                f"挪动 {allocation['moved_cost']:.4f}，CPA "
                f"{allocation['cpa_before']:.6f} → {allocation['cpa_after']:.6f}"
                if allocation.get("moves") and allocation.get("cpa_after") is not None
                else allocation.get("note", "无建议")
            )
        )
        return {"moves": len(allocation.get("moves", [])), "moved_cost": allocation.get("moved_cost")}


class ExperimentStage:
    """实验设计：按当前基准算样本量、时长与判定边界。"""

    name = "experiment"

    def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        metrics = ctx.data.get("metrics")
        daily = ctx.data.get("daily")
        if metrics is None:
            raise RuntimeError("experiment 阶段缺少 metrics：请先运行 metrics 阶段")

        cfg = ctx.config.experiment
        baseline = metrics["derived"].get(cfg.baseline_metric)
        daily_denominator = None
        if daily is not None and not daily.empty:
            daily_denominator = float(pd.to_numeric(daily["impressions"], errors="coerce").mean())

        plan = design_experiment(
            metric=cfg.baseline_metric,
            baseline_rate=baseline or 0.0,
            daily_denominator=daily_denominator,
            alpha=cfg.alpha,
            power=cfg.power,
            mde_rel=cfg.mde_rel,
            looks=cfg.looks,
        )
        ctx.data["experiment_plan"] = plan
        ctx.dump_json("experiment/plan.json", plan)
        ctx.log(f"实验设计：{plan.get('verdict')}")
        return {"metric": plan.get("metric"), "days": plan.get("days_required"),
                "sample_size_per_arm": plan.get("sample_size_per_arm")}


class ComposeStage:
    """成稿：AI 叙述（带数字回指守卫）+ 完整诊断报告。"""

    name = "compose"

    def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        metrics = ctx.data.get("metrics")
        if metrics is None:
            raise RuntimeError("compose 阶段缺少 metrics：请先运行 metrics 阶段")
        cfg = ctx.config

        payload = build_narrative_payload(
            account=cfg.run.account,
            metrics=metrics,
            quality=ctx.data["quality"],
            concentration=ctx.data["concentration"],
            findings=ctx.data["findings"],
            summary=ctx.data["summary"],
            spend_shift=ctx.data.get("allocation"),
            cost_unit=(
                "口径单位"
                if str((ctx.data.get("source_meta") or {}).get("source", "")) in {"sample", "criteo"}
                else "元"
            ),
        )
        narrative = narrate(
            payload,
            cfg.llm,
            extra_allowed=[cfg.diagnose.min_clicks, cfg.diagnose.min_conversions,
                           cfg.diagnose.min_impressions],
        )
        ctx.data["narrative"] = narrative
        ctx.dump_json("narrative.json", {
            "mode": narrative.get("mode"),
            "guard": narrative.get("guard"),
            "text": narrative.get("text"),
        })

        source_label = str((ctx.data.get("source_meta") or {}).get("source", ""))
        boundary = PUBLIC_DATASET_BOUNDARY if source_label in {"sample", "criteo"} else None

        text = build_report(
            account=cfg.run.account,
            metrics=metrics,
            quality=ctx.data["quality"],
            concentration=ctx.data["concentration"],
            findings=ctx.data["findings"],
            summary=ctx.data["summary"],
            campaign_table=ctx.data["campaign_table"],
            hourly=ctx.data["hourly"],
            daily=ctx.data["daily"],
            allocation=ctx.data.get("allocation") or {},
            experiment_plan=ctx.data.get("experiment_plan") or {},
            narrative=narrative,
            normalize_report=ctx.data.get("normalize_report"),
            charts=ctx.data.get("charts"),
            top_n=cfg.run.top_n,
            data_source=(ctx.data.get("source_meta") or {}).get("path", ""),
            sql_appendix=render_sql_appendix(cfg.run.top_n),
            data_boundary=boundary,
        )
        path = write_report(text, ctx.path("report.md"))
        ctx.log(f"报告成稿：{path}（叙述模式 {narrative.get('mode')}）")
        return {"report": str(path), "narrative_mode": narrative.get("mode")}


# ---------------------------------------------------------------------------
# 流水线装配与运行
# ---------------------------------------------------------------------------


@dataclass
class Pipeline:
    """按顺序执行阶段；任一阶段抛出异常即中止（由调用方处理）。"""

    stages: List[Any]

    def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        results: List[Dict[str, Any]] = []
        for stage in self.stages:
            payload = stage.run(ctx)
            results.append({"stage": stage.name, **payload})
        return {
            "stages": results,
            "output_dir": str(ctx.output_dir),
            "logs": list(ctx.logs),
            "latest_period": latest_period(ctx.data["frame"]) if "frame" in ctx.data else None,
            "headline": {
                "kpi": ctx.data["metrics"]["derived"] if "metrics" in ctx.data else {},
                "findings": ctx.data["summary"] if "summary" in ctx.data else {},
            },
        }


def build_pipeline(*, with_charts: bool = True) -> Pipeline:
    """默认装配：九个阶段，图表可关闭（无头/无 matplotlib 环境）。"""
    stages: List[Any] = [
        CollectStage(), CleanStage(), QualityStage(), MetricsStage(), DiagnoseStage(),
    ]
    if with_charts:
        stages.append(ChartsStage())
    stages.extend([AllocationStage(), ExperimentStage(), ComposeStage()])
    return Pipeline(stages=stages)


def run_pipeline(
    config: AppConfig,
    output_dir: Optional[str | Path] = None,
    *,
    with_charts: bool = True,
) -> Dict[str, Any]:
    """便捷入口：按配置运行完整流水线。

    Args:
        config: 应用配置。
        output_dir: 输出目录；None 时取 ``config.run.out_dir``（相对仓库根）。
        with_charts: 是否渲染图表。

    Returns:
        运行结果字典（含 stages / logs / headline）。
    """
    out = Path(output_dir) if output_dir else config.resolve(config.run.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ctx = PipelineContext(config=config, output_dir=out)
    pipeline = build_pipeline(with_charts=with_charts)
    result = pipeline.run(ctx)
    result["output_dir"] = str(out)
    return result
