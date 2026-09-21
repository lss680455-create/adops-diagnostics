# -*- coding: utf-8 -*-
"""数据质量体检：把「这份投放数据能不能用来做决策」变成可核验的检查项。

为什么单独一层：投放复盘最常见的翻车不是结论算错，而是**输入本身有问题**——
消耗被重复导出、点击回传漏了一部分、转化标记没回填。这类问题不排掉，后面
所有 CPA 都是错的，而报表上看起来一切正常。

每个检查项输出 ``ok / warn / fail`` + 阈值 + 实际值，报告里逐条列明。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..config import QualityConfig

_CHECK_ORDER = ("impressions", "clicks", "conversions", "cost", "campaign_id", "date")


def _missing_rates(frame: pd.DataFrame) -> Dict[str, float]:
    rates: Dict[str, float] = {}
    for column in frame.columns:
        if column == "user_id":
            continue
        rates[column] = float(frame[column].isna().mean())
    return rates


def cost_outlier_bounds(cost: pd.Series, iqr_multiplier: float = 3.0) -> Optional[Dict[str, float]]:
    """用 IQR 法给出消耗的异常上界；样本不足或全为 0 时返回 None。

    投放数据里的消耗是长尾分布（少数曝光贵得多），所以用 IQR 而不是均值±σ：
    均值会被长尾自己拉高，反而检不出异常。
    """
    values = pd.to_numeric(cost, errors="coerce").dropna()
    values = values[values > 0]
    if len(values) < 20:
        return None
    q1 = float(values.quantile(0.25))
    q3 = float(values.quantile(0.75))
    iqr = q3 - q1
    if iqr <= 0:
        return None
    upper = q3 + iqr_multiplier * iqr
    return {"q1": q1, "q3": q3, "iqr": iqr, "upper": upper}


def cost_check_basis(frame: pd.DataFrame) -> Tuple[pd.Series, str]:
    """决定消耗异常检测的统计对象，并返回 ``(序列, 口径说明)``。

    异常检测不能以「单条曝光」为对象：一次曝光花多少钱由竞价瞬时决定，天然是
    离散长尾分布，把它当异常检测对象会把正常的高价曝光全判成异常（实测能到
    7%）。也不适合以「跨计划的消耗分布」为对象：计划之间的消耗差异是结构差异，
    不是数据错误。真正该预警的是**时间口径**的异常——某一天的钱明显跑偏，
    通常意味着漏跑、超跑或导出不全。因此优先按日汇总，其次按计划汇总，
    两者都缺才退回原始行。
    """
    for dimension, label in (("date", "按日汇总"), ("campaign_id", "按计划汇总")):
        if dimension in frame.columns and frame[dimension].notna().any():
            grouped = (
                pd.to_numeric(frame["cost"], errors="coerce").fillna(0.0)
                .groupby(frame[dimension], dropna=True).sum()
            )
            grouped = grouped[grouped.index.notna()]
            if len(grouped) >= 20:
                return grouped, label
    return pd.to_numeric(frame["cost"], errors="coerce").fillna(0.0), "按明细行"


def assess_quality(
    frame: pd.DataFrame,
    report: Any,
    cfg: QualityConfig,
) -> Dict[str, Any]:
    """生成数据质量体检结果。

    Args:
        frame: 清洗后的统一计数表。
        report: :class:`adops.clean.normalize.NormalizeReport`。
        cfg: 质量门禁阈值。

    Returns:
        结构化体检结果：字段完整率、重复率、一致性、消耗异常、时间覆盖、
        以及一组带 ``ok/warn/fail`` 的门禁结论。
    """
    rows = int(len(frame))
    checks: List[Dict[str, Any]] = []
    missing = _missing_rates(frame)

    def gate(name: str, ok: bool, warn: bool, value: Any, threshold: Any, message: str) -> None:
        status = "ok" if ok else ("warn" if warn else "fail")
        checks.append({
            "check": name,
            "status": status,
            "value": value,
            "threshold": threshold,
            "message": message,
        })

    # 1) 必填字段完整率
    for column in ("impressions", "clicks"):
        rate = missing.get(column, 1.0)
        gate(
            f"完整率:{column}", rate <= cfg.max_missing_rate, rate <= cfg.max_missing_rate * 3,
            round(rate, 6), cfg.max_missing_rate,
            f"{column} 缺失率 {rate * 100:.2f}%",
        )

    # 2) 维度字段完整率（维度缺失会让拆分失真，但不算致命）
    for column in ("campaign_id", "date"):
        if column not in frame.columns:
            gate(f"完整率:{column}", False, True, None, cfg.max_missing_rate, f"{column} 整列缺失")
            continue
        rate = missing.get(column, 1.0)
        gate(
            f"完整率:{column}", rate <= cfg.max_missing_rate, rate <= 0.2,
            round(rate, 6), cfg.max_missing_rate,
            f"{column} 缺失率 {rate * 100:.2f}%",
        )

    # 3) 重复率
    duplicate_rate = float(getattr(report, "duplicate_rate", 0.0))
    gate(
        "重复率", duplicate_rate <= cfg.max_duplicate_rate, duplicate_rate <= cfg.max_duplicate_rate * 5,
        round(duplicate_rate, 6), cfg.max_duplicate_rate,
        f"完全重复行 {getattr(report, 'duplicate_rows', 0)} 行（{duplicate_rate * 100:.2f}%）",
    )

    # 4) 内部一致性（清洗阶段已钳位，这里报告原始冲突量）
    for field, label in (("inconsistent_clicks", "点击 > 曝光"), ("inconsistent_conversions", "转化 > 点击")):
        count = int(getattr(report, field, 0))
        rate = count / rows if rows else 0.0
        gate(
            f"一致性:{label}", count == 0, rate <= 0.01, count, 0,
            f"{label} 的记录 {count} 行（{rate * 100:.2f}%）",
        )

    negative_rows = int(getattr(report, "negative_cost_rows", 0))
    gate("一致性:负消耗", negative_rows == 0, negative_rows <= rows * 0.001, negative_rows, 0,
         f"消耗为负的记录 {negative_rows} 行（已置空）")

    # 5) 消耗异常值（按汇总口径检测，见 cost_check_basis 的说明）
    basis, basis_label = cost_check_basis(frame) if "cost" in frame.columns else (None, "")
    bounds = cost_outlier_bounds(basis, cfg.cost_outlier_iqr) if basis is not None else None
    if bounds:
        outliers = int((pd.to_numeric(basis, errors="coerce").dropna() > bounds["upper"]).sum())
        total = int(len(basis))
        rate = outliers / total if total else 0.0
        gate(f"消耗异常值（{basis_label}）", rate <= 0.01, rate <= 0.05, outliers,
             f"上界 {bounds['upper']:.6g}",
             f"超出 IQR 上界的 {basis_label}对象 {outliers} / {total} 个（{rate * 100:.2f}%）")
    else:
        gate("消耗异常值", True, True, None, "—", "样本不足或消耗无波动，跳过异常检测")

    # 6) 零消耗曝光占比（0 消耗曝光通常是竞价未成交或日志口径问题）
    if "cost" in frame.columns and rows:
        zero_rate = float((pd.to_numeric(frame["cost"], errors="coerce").fillna(0.0) <= 0).mean())
        gate("零消耗占比", zero_rate <= cfg.zero_cost_alert_rate, True, round(zero_rate, 6),
             cfg.zero_cost_alert_rate,
             f"零消耗行占 {zero_rate * 100:.2f}%（竞价未成交或口径问题需确认）")

    # 7) 时间覆盖率
    date_column = frame["date"] if "date" in frame.columns else None
    distinct_days = int(date_column.dropna().nunique()) if date_column is not None else 0
    gate("时间覆盖", distinct_days >= 7, distinct_days >= 1, distinct_days, "≥7 天",
         f"覆盖 {distinct_days} 天（做趋势类诊断至少需要 7 天）")

    # 8) 归因覆盖率（有归因字段才检查）
    # 注意分级：归因覆盖率低说明「回传链路可能有问题」，属于业务发现而非数据错误，
    # 因此只在极低时才判 fail；常规偏低交给诊断层 R02 出结论，避免同一件事报两次、
    # 还把整份报告判成「不可用」。
    if "attributed_conversions" in frame.columns:
        conversions = float(pd.to_numeric(frame["conversions"], errors="coerce").fillna(0).sum())
        attributed = float(pd.to_numeric(frame["attributed_conversions"], errors="coerce").fillna(0).sum())
        rate = (attributed / conversions) if conversions else 1.0
        gate("归因覆盖率", rate >= 0.8, rate >= 0.3, round(rate, 6), "≥0.8 正常；<0.3 判为数据错误",
             f"归因转化 {attributed:,.0f} / 全部转化 {conversions:,.0f} = {rate * 100:.2f}%")

    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    if failed:
        verdict = "不可用：存在确定性数据错误，结论会被污染"
    elif warned:
        verdict = "可用但有风险：存在需要人工确认的口径问题"
    else:
        verdict = "可用：各项检查通过"

    return {
        "rows": rows,
        "columns": list(frame.columns),
        "missing_rates": missing,
        "duplicate_rate": duplicate_rate,
        "cost_bounds": bounds,
        "distinct_days": distinct_days,
        "layout": getattr(report, "layout", "unknown"),
        "time_basis": getattr(report, "time_basis", "unknown"),
        "checks": checks,
        "failed": len(failed),
        "warned": len(warned),
        "verdict": verdict,
    }


_STATUS_ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌"}


def render_quality_md(quality: Dict[str, Any]) -> str:
    """把质量体检结果渲染成 Markdown 片段（供报告层拼装）。"""
    lines = [
        f"**体检结论：{quality['verdict']}**",
        "",
        f"- 记录数：{quality['rows']:,}　维度布局：`{quality['layout']}`　时间口径：`{quality['time_basis']}`",
        f"- 覆盖天数：{quality['distinct_days']}　完全重复率：{quality['duplicate_rate'] * 100:.2f}%",
        f"- 检查项：{len(quality['checks'])} 项，失败 {quality['failed']} 项，警告 {quality['warned']} 项",
        "",
        "| 检查项 | 状态 | 实际值 | 阈值 | 说明 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in quality["checks"]:
        value = "—" if check["value"] is None else check["value"]
        if isinstance(value, float):
            value = f"{value:.4f}" if abs(value) < 1 else f"{value:,.2f}"
        lines.append(
            f"| {check['check']} | {_STATUS_ICON.get(check['status'], '')} {check['status']} "
            f"| {value} | {check['threshold']} | {check['message']} |"
        )
    lines.append("")
    return "\n".join(lines)
