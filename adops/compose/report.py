# -*- coding: utf-8 -*-
"""成稿层：把各阶段产物组装成一份可以直接发出去的诊断报告。

报告的对象是**投放负责人**，不是工程师，所以每个结论后面都跟着三件事：
证据、动作、影响面。数字一律从上游产物里取，本层不做任何新的计算——
「报告里出现的数，一定能指回某个中间产物文件」。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import pandas as pd

from ..metrics.ads import FUNNEL_SPECS, safe_div
from ..util import money, num, pct, ratio, safe_float, signed_pct

#: 计划效率分层标签
VERDICT_EXCELLENT = "效率优"
VERDICT_OK = "持平"
VERDICT_POOR = "效率低"
VERDICT_NO_CONVERSION = "零转化"
VERDICT_SAMPLE = "样本不足"


def campaign_verdicts(
    table: pd.DataFrame,
    global_cpa: Optional[float],
    *,
    min_conversions: int = 10,
    min_clicks: int = 30,
    poor_multiplier: float = 1.5,
) -> List[str]:
    """给每个计划打一个确定性标签（供报告表格使用）。

    判定顺序刻意如此：先看有没有转化（零转化是最确定的问题），再看样本量
    （样本不足不下结论），最后才比 CPA——避免把「样本小所以 CPA 高」误判成
    「效率差」。
    """
    verdicts: List[str] = []
    for row in table.to_dict(orient="records"):
        conversions = safe_float(row.get("conversions"))
        clicks = safe_float(row.get("clicks"))
        cpa_value = row.get("cpa")
        if conversions <= 0:
            verdicts.append(VERDICT_NO_CONVERSION if clicks >= min_clicks else VERDICT_SAMPLE)
            continue
        if conversions < min_conversions or global_cpa in (None, 0) or cpa_value is None:
            verdicts.append(VERDICT_SAMPLE)
            continue
        if cpa_value >= global_cpa * poor_multiplier:
            verdicts.append(VERDICT_POOR)
        elif cpa_value <= global_cpa / poor_multiplier:
            verdicts.append(VERDICT_EXCELLENT)
        else:
            verdicts.append(VERDICT_OK)
    return verdicts


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(str(h) for h in headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join("" if cell is None else str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _kpi_section(metrics: Mapping[str, Any]) -> str:
    rows = [
        [item["label"], item["display"], item["hint"]]
        for item in metrics.get("indicators", [])
    ]
    return _md_table(["指标", "数值", "口径"], rows)


def _funnel_section(metrics: Mapping[str, Any]) -> str:
    rows = []
    for step in metrics.get("funnel", []):
        rows.append([step["label"], step["display"], step.get("step_display") or "—"])
    return _md_table(["环节", "数量", "本环节转化率"], rows)


def _campaign_section(table: pd.DataFrame, global_cpa: Optional[float], top_n: int) -> str:
    if table is None or table.empty:
        return "_没有可用的计划维度数据。_"
    head = table.head(top_n).copy()
    head["verdict"] = campaign_verdicts(head, global_cpa)
    rows = []
    for row in head.to_dict(orient="records"):
        rows.append([
            row.get("campaign_id"),
            money(row.get("cost"), 2),
            pct(row.get("spend_share")),
            num(row.get("impressions")),
            num(row.get("clicks")),
            num(row.get("conversions")),
            pct(row.get("ctr")),
            pct(row.get("cvr")),
            money(row.get("cpc"), 4),
            money(row.get("cpm"), 4),
            money(row.get("cpa"), 4),
            row.get("verdict"),
        ])
    return _md_table(
        ["计划", "消耗", "消耗占比", "曝光", "点击", "转化", "CTR", "CVR", "CPC", "CPM", "CPA", "判定"],
        rows,
    )


def _hourly_section(hourly: pd.DataFrame) -> str:
    if hourly is None or hourly.empty:
        return "_没有小时维度的数据。_"
    head = hourly.sort_values("ctr", ascending=False).head(5)
    tail = hourly.sort_values("ctr").head(3)
    rows = []
    for row in pd.concat([head, tail]).drop_duplicates(subset=["hour"]).to_dict(orient="records"):
        rows.append([
            f"{int(safe_float(row.get('hour'))):02d}:00",
            num(row.get("impressions")),
            pct(row.get("ctr")),
            money(row.get("cpa"), 4),
        ])
    return _md_table(["时段", "曝光", "CTR", "CPA"], rows)


def _findings_section(findings: Sequence[Any]) -> str:
    if not findings:
        return "_未触发任何诊断规则：当前结构与效率均在阈值内。_"
    blocks: List[str] = []
    for finding in findings:
        level = {"high": "高", "medium": "中", "low": "低"}.get(finding.level, finding.level)
        block = [
            f"### {finding.rule_id}　[{level}] {finding.title}",
            "",
            f"- **对象**：{finding.subject}",
            f"- **影响消耗占比**：{pct(getattr(finding, 'spend_share', 0.0))}　"
            f"**优先级分**：{getattr(finding, 'priority', 0.0):.4f}",
            "- **证据**：",
        ]
        block.extend(f"  - {line}" for line in finding.evidence)
        block.append(f"- **建议动作**：{finding.action}")
        block.append(f"- **影响说明**：{finding.impact}")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def _allocation_section(allocation: Mapping[str, Any]) -> str:
    if not allocation or not allocation.get("moves"):
        note = (allocation or {}).get("note", "无需调仓")
        return f"_本次未给出调仓建议：{note}_"
    rows = []
    for move in allocation["moves"]:
        rows.append([
            move["campaign_id"],
            money(move["cost"], 2),
            money(move["cpa"], 4),
            f"{move['delta']:+,.4f}",
            signed_pct(move.get("delta_ratio")),
            move["reason"],
        ])
    table = _md_table(["计划", "当前消耗", "CPA", "调整额", "调整比例", "理由"], rows)
    summary = (
        f"- 预算总额不变，挪动 **{money(allocation.get('moved_cost'), 4)}**；"
        f"参与调仓 **{len(allocation['moves'])}** 个计划\n"
        f"- CPA：{money(allocation.get('cpa_before'), 4)} → "
        f"{money(allocation.get('cpa_after'), 4)}"
        f"（{signed_pct(allocation.get('improvement'))}，一阶近似）\n"
        f"- 中位 CPA 基准：{money(allocation.get('baseline_cpa'), 4)}"
    )
    return "\n\n".join([summary, "", table, "", f"> ⚠️ {allocation.get('assumption')}"])


def _experiment_section(plan: Mapping[str, Any]) -> str:
    if not plan:
        return "_未生成实验方案。_"
    rows = [
        ["主指标", plan.get("metric_label")],
        ["基准值", pct(plan.get("baseline_rate"))],
        ["期望检出提升", signed_pct(plan.get("target_relative_lift"))],
        ["绝对差异（MDE）", pct(plan.get("mde_absolute"))],
        ["每组样本量", num(plan.get("sample_size_per_arm"))],
        ["总样本量", num(plan.get("sample_size_total"))],
        ["每日可得样本", num(plan.get("daily_denominator"))],
        ["预计运行天数", num(plan.get("days_required"))],
        ["实际可检出（相对）", signed_pct(plan.get("achievable_relative_mde"))],
        ["判定方式", "序贯（O'Brien-Fleming）" if (plan.get("looks") or 1) > 1 else "单次判定"],
    ]
    body = _md_table(["项", "值"], rows)
    boundary_rows = []
    for boundary in plan.get("boundaries", []):
        boundary_rows.append([
            num(boundary.get("look", 0)),
            pct(boundary.get("information_fraction")),
            pct(boundary.get("alpha")),
            "—" if boundary.get("z") is None else f"{boundary['z']:.4f}",
        ])
    boundary = ""
    if boundary_rows:
        boundary = "\n\n**判定边界**\n\n" + _md_table(
            ["第几次查看", "信息比例", "名义 α", "临界 z"], boundary_rows
        )
    notes = "\n".join(f"- {n}" for n in plan.get("notes", []))
    return "\n\n".join([body, boundary, f"**结论**：{plan.get('verdict', '—')}", notes])


def _metric_definitions() -> str:
    rows = [
        ["CTR", "点击 / 曝光", "素材与定向的吸引力"],
        ["CVR", "转化 / 点击", "承接页与转化链路的效率"],
        ["曝光转化率", "转化 / 曝光", "全链路效率（CTR × CVR）"],
        ["CPM", "消耗 / 曝光 × 1000", "流量成本，成本的源头"],
        ["CPC", "消耗 / 点击", "CPM / (CTR × 1000)"],
        ["CPA", "消耗 / 转化", "CPC / CVR"],
        ["归因覆盖率", "归因转化 / 全部转化", "回传链路完整性"],
        ["HHI", "各计划消耗占比的平方和", "消耗集中度，> 0.25 视为偏集中"],
        ["基尼系数", "消耗分布不均衡度", "比 HHI 更敏感于长尾"],
    ]
    return _md_table(["指标", "计算口径", "业务含义"], rows)


def _billing_identity_section(metrics: Mapping[str, Any]) -> str:
    """CPM 恒等式自检：``CPM == CPA × CVR × CTR × 1000``。

    这条恒等式在数学上必然成立，所以它检验的不是数据对错，而是**报表口径
    有没有被改动过**（例如有人手改过某列、或聚合层级不一致）。
    """
    derived = metrics.get("derived", {})
    cpm_value = derived.get("cpm")
    cpa_value = derived.get("cpa")
    ctr_value = derived.get("ctr")
    cvr_value = derived.get("cvr")
    implied = None
    if None not in (cpa_value, ctr_value, cvr_value):
        implied = float(cpa_value) * float(ctr_value) * float(cvr_value) * 1000.0
    gap = safe_div((cpm_value or 0) - (implied or 0), implied) if implied not in (None, 0) else None
    rows = [
        ["实际 CPM", money(cpm_value, 6)],
        ["反推 CPM（CPA × CVR × CTR × 1000）", money(implied, 6)],
        ["相对偏离", pct(gap, 4)],
        ["判定", "口径自洽" if gap is None or abs(gap) < 1e-6 else "口径存在偏离，需核对"],
    ]
    return _md_table(["项", "值"], rows)


def build_report(
    *,
    account: str,
    metrics: Mapping[str, Any],
    quality: Mapping[str, Any],
    concentration: Mapping[str, Any],
    findings: Sequence[Any],
    summary: Mapping[str, Any],
    campaign_table: pd.DataFrame,
    hourly: pd.DataFrame,
    daily: pd.DataFrame,
    allocation: Mapping[str, Any],
    experiment_plan: Mapping[str, Any],
    narrative: Mapping[str, Any],
    normalize_report: Any,
    charts: Optional[Mapping[str, Path]] = None,
    top_n: int = 10,
    data_source: str = "",
    sql_appendix: Optional[str] = None,
    data_boundary: Optional[str] = None,
) -> str:
    """组装完整 Markdown 报告。"""
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    dates = []
    if daily is not None and not daily.empty:
        dates = [str(v) for v in daily["date"].tolist()]
    period = f"{dates[0]} ~ {dates[-1]}" if dates else "—"

    lines: List[str] = [
        "# 广告投放数据诊断报告",
        "",
        f"- **账户**：{account}",
        f"- **数据范围**：{period}（{quality.get('distinct_days', 0)} 天，{num(quality.get('rows'))} 条记录）",
        f"- **数据来源**：{data_source or '—'}　**布局**：{quality.get('layout')}　**时间口径**：{quality.get('time_basis')}",
        f"- **生成时间**：{generated}",
        "- **口径版本**：v1.0（所有比率均为先加总再相除，见附录口径表）",
        "",
        "---",
        "",
        "## 0. 结论摘要",
        "",
        narrative.get("text", ""),
        "",
    ]

    if narrative.get("mode") == "template":
        reason = (narrative.get("guard") or {}).get("fallback_reason")
        if reason:
            lines.append(f"> 叙述由确定性模板生成：{reason}")
        else:
            lines.append("> 叙述由确定性模板生成（未启用 LLM，结论与启用时一致，仅行文更朴素）。")
    else:
        guard = narrative.get("guard") or {}
        lines.append(
            f"> 叙述由模型生成（`{narrative.get('model', '')}`），"
            f"已通过数字回指校验：{guard.get('checked', 0)} 个数字全部可在证据表中找到。"
        )
    lines.append("")

    lines.extend([
        "## 1. 核心指标",
        "",
        _kpi_section(metrics),
        "",
        "### 漏斗",
        "",
        _funnel_section(metrics),
        "",
        "### 计费口径自检",
        "",
        _billing_identity_section(metrics),
        "",
        "## 2. 数据质量体检",
        "",
        (quality.get("verdict") or "") + f"（检查 {len(quality.get('checks', []))} 项，"
        f"失败 {quality.get('failed', 0)} 项，警告 {quality.get('warned', 0)} 项）",
        "",
    ])
    lines.append(_md_table(
        ["检查项", "状态", "实际值", "阈值", "说明"],
        [[c["check"], c["status"], c["value"], c["threshold"], c["message"]]
         for c in quality.get("checks", [])],
    ))
    lines.append("")
    if normalize_report is not None:
        lines.extend([
            "**清洗审计**",
            "",
            f"- 原始记录：{num(getattr(normalize_report, 'raw_rows', None))}　"
            f"清洗后：{num(getattr(normalize_report, 'rows', None))}",
            f"- 完全重复行：{num(getattr(normalize_report, 'duplicate_rows', None))}",
            f"- 点击 > 曝光：{num(getattr(normalize_report, 'inconsistent_clicks', None))}　"
            f"转化 > 点击：{num(getattr(normalize_report, 'inconsistent_conversions', None))}　"
            f"负消耗：{num(getattr(normalize_report, 'negative_cost_rows', None))}",
            f"- 未识别的来源列：{'、'.join(getattr(normalize_report, 'unrecognized', []) or []) or '无'}",
            "",
        ])

    lines.extend([
        "## 3. 计划效率",
        "",
        _campaign_section(campaign_table, metrics.get("derived", {}).get("cpa"), top_n),
        "",
        "**消耗结构**",
        "",
        _md_table(
            ["指标", "值"],
            [
                ["HHI", num(concentration.get("hhi"), 4)],
                ["前 1 计划占比", pct(concentration.get("top1_share"))],
                ["前 3 计划占比", pct(concentration.get("top3_share"))],
                ["基尼系数", num(concentration.get("gini"), 4)],
            ],
        ),
        "",
        "## 4. 分时洞察（按 CTR 排序，取头部与尾部）",
        "",
        _hourly_section(hourly),
        "",
        "## 5. 诊断发现（按优先级排序）",
        "",
        f"共 {summary.get('total', 0)} 项：高 {summary.get('counts', {}).get('high', 0)}、"
        f"中 {summary.get('counts', {}).get('medium', 0)}、低 {summary.get('counts', {}).get('low', 0)}；"
        f"影响消耗占比合计 {pct(summary.get('affected_spend_share'))}"
        + ("（原始合计 " + pct(summary.get("affected_spend_share_raw")) + "，**各项之间存在重叠，不可相加**）"
           if summary.get("overlapping") else "") + "。",
        "",
        _findings_section(findings),
        "",
        "## 6. 预算再分配建议（确定性算法，可复算）",
        "",
        _allocation_section(allocation),
        "",
        "## 7. 下一轮实验设计",
        "",
        _experiment_section(experiment_plan),
        "",
    ])

    if allocation.get("moves"):
        lines.extend([
            "### 配套实验：验证调仓是否真的有效",
            "",
            "把上面调仓幅度最大的一条当作实验组、维持原样的一条当作对照组，"
            "按第 7 节给出的样本量跑一次对照实验；主指标用 CPA，护栏指标用消耗与转化数",
            "（防止主指标变好但总转化下滑）。",
            "",
        ])

    if charts:
        lines.extend(["## 8. 图表", ""])
        for name in sorted(charts):
            lines.append(f"- `{Path(charts[name]).name}`")
        lines.append("")

    lines.extend([
        "## 9. 附录",
        "",
        "### 指标口径",
        "",
        _metric_definitions(),
        "",
    ])
    if data_boundary:
        lines.extend(["### 数据边界与口径说明", "", data_boundary, ""])
    if sql_appendix:
        lines.extend(["### 复核用 SQL", "", sql_appendix, ""])
    lines.extend([
        "### 复现方式",
        "",
        "```bash",
        "python -m adops run --source sample --out outputs/report",
        "python -m pytest -q",
        "```",
        "",
        "报告中的每个数字都由 `outputs/` 下的中间产物（`clean/`、`metrics/`、`diagnose/`）"
        "计算得到，可从中间产物逐步复算。",
        "",
    ])
    return "\n".join(lines)


def write_report(text: str, path: str | Path) -> Path:
    """把报告写出为 Markdown。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
