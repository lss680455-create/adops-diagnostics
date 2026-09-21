# -*- coding: utf-8 -*-
"""聚合层：把事件级明细收敛成维度表，所有比率都在聚合后再算。

口径纪律：**先加总再算率**（不能对各行的比率求平均），否则小样本行会被
同等加权，CTR / CPA 全错。本模块只做聚合，不引入任何比率计算逻辑。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from .ads import cpa, cpc, cpm, ctr, cvr, ipv, safe_div

#: 可用于拆分维度的字段（按优先级排序，报告里依次展示）
DIMENSION_FIELDS: Sequence[str] = ("campaign_id", "channel", "placement", "creative")

_COUNT_COLUMNS = ("impressions", "clicks", "conversions", "cost", "attributed_conversions")


def overall_totals(frame: pd.DataFrame) -> Dict[str, float]:
    """全量加总。"""
    totals: Dict[str, float] = {}
    for column in _COUNT_COLUMNS:
        if column in frame.columns:
            totals[column] = float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())
    totals["rows"] = float(len(frame))
    return totals


def aggregate(frame: pd.DataFrame, by: Iterable[str]) -> pd.DataFrame:
    """按给定维度聚合，附带常用派生指标。

    Args:
        frame: 规范计数表（含 impressions / clicks / conversions / cost）。
        by: 维度字段名。

    Returns:
        聚合后的 DataFrame，含原始计数与 ctr / cvr / cpc / cpm / cpa 列，
        按消耗降序排列（投放复盘默认看消耗最大的先）。
    """
    keys = [k for k in by if k in frame.columns]
    if not keys:
        raise ValueError(f"聚合维度都不在表中：{list(by)}")

    working = frame.copy()
    for column in _COUNT_COLUMNS:
        if column not in working.columns:
            working[column] = 0.0
        working[column] = pd.to_numeric(working[column], errors="coerce").fillna(0.0)

    grouped = working.groupby(keys, dropna=False, observed=True)[list(_COUNT_COLUMNS)].sum().reset_index()

    grouped["ctr"] = [ctr(c, i) for c, i in zip(grouped["clicks"], grouped["impressions"])]
    grouped["cvr"] = [cvr(c, k) for c, k in zip(grouped["conversions"], grouped["clicks"])]
    grouped["ipv"] = [ipv(c, i) for c, i in zip(grouped["conversions"], grouped["impressions"])]
    grouped["cpc"] = [cpc(s, c) for s, c in zip(grouped["cost"], grouped["clicks"])]
    grouped["cpm"] = [cpm(s, i) for s, i in zip(grouped["cost"], grouped["impressions"])]
    grouped["cpa"] = [cpa(s, k) for s, k in zip(grouped["cost"], grouped["conversions"])]
    grouped["spend_share"] = _share(grouped["cost"])
    grouped = grouped.sort_values("cost", ascending=False).reset_index(drop=True)
    return grouped


def _share(series: pd.Series) -> List[Optional[float]]:
    total = float(pd.to_numeric(series, errors="coerce").fillna(0).sum())
    if total <= 0:
        return [None] * len(series)
    return [safe_div(v, total) for v in series]


def campaign_table(frame: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """广告计划效率表（按消耗降序取前 N）。"""
    return aggregate(frame, ["campaign_id"]).head(top_n).reset_index(drop=True)


def daily_totals(frame: pd.DataFrame) -> pd.DataFrame:
    """按日加总，供趋势图与「最近 N 天恶化」规则使用。"""
    if "date" not in frame.columns:
        return pd.DataFrame(columns=["date", "impressions", "clicks", "conversions", "cost"])
    return aggregate(frame, ["date"]).sort_values("date").reset_index(drop=True)


def hourly_totals(frame: pd.DataFrame) -> pd.DataFrame:
    """按小时加总，供时段机会识别使用（跨日聚合，忽略日期）。"""
    if "hour" not in frame.columns:
        return pd.DataFrame(columns=["hour", "impressions", "clicks", "conversions", "cost"])
    return aggregate(frame, ["hour"]).sort_values("hour").reset_index(drop=True)


def dimension_tables(frame: pd.DataFrame, top_n: int = 10) -> Dict[str, List[Dict[str, Any]]]:
    """所有可用维度的效率摘要（供报告层与规则层消费）。

    Returns:
        ``{dimension: [ {维度值, impressions, clicks, conversions, cost, ctr, cvr, cpa, spend_share}, ... ]}``
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    for dimension in DIMENSION_FIELDS:
        if dimension not in frame.columns:
            continue
        table = aggregate(frame, [dimension])
        if table.empty:
            continue
        out[dimension] = _records(table.head(top_n))
    return out


def _records(table: pd.DataFrame) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for row in table.to_dict(orient="records"):
        records.append({
            key: (None if pd.isna(value) else value)
            for key, value in row.items()
        })
    return records
