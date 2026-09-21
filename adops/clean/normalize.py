# -*- coding: utf-8 -*-
"""清洗层：把任意来源的投放表收敛成统一计数表。

统一的内部口径只有一张表：**每行是一个维度组合，含计数与消耗**。

============================  ==================================================
列                            含义
============================  ==================================================
``date`` / ``hour``           时间维度（绝对日期，或相对天数 ``D001`` 形式）
``campaign_id`` 等            维度列（缺失则该维度不参与拆分，不报错）
``impressions``               曝光量
``clicks``                    点击量
``conversions``               转化数
``cost``                      消耗（元，口径同来源）
``attributed_conversions``    被平台归因的转化数（缺失记 NaN，不是 0）
============================  ==================================================

两件事在入口处做掉，后面所有指标就不必再操心：

1. **识别布局**：曝光级日志（每行一次曝光，click 是 0/1 标记）与聚合报表
   （每行已含曝光量/点击量）走不同路径，但出口同构；
2. **时间口径**：绝对时间戳归一化为日期；公开数据集常见的「距今秒数」这类
   相对时间归一化为 ``D001`` 形式的相对天，并在报告里明确标注 —— 宁可在报告
   里写清口径，也不假装它是真实日历日期。

修正动作（钳位、置空）全部记进 :class:`NormalizeReport`，不静默篡改数据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..collect.schema import detect_layout, map_columns

COUNT_COLUMNS: Tuple[str, ...] = (
    "impressions", "clicks", "conversions", "cost", "attributed_conversions",
)
CONTEXT_COLUMNS: Tuple[str, ...] = ("campaign_id", "channel", "placement", "creative")
PASSTHROUGH_COLUMNS: Tuple[str, ...] = ("user_id", "click_nb", "time_since_last_click")
KEEP_COLUMNS: Tuple[str, ...] = (
    ("date", "hour") + CONTEXT_COLUMNS + COUNT_COLUMNS + PASSTHROUGH_COLUMNS
)

_EPOCH_FLOOR = 1_000_000_000  # 大于此值视为绝对时间戳（秒），否则视为相对秒数
_SECONDS_PER_DAY = 86_400


@dataclass
class NormalizeReport:
    """清洗阶段的审计信息（进报告，不做静默修正）。"""

    layout: str = "aggregated"
    raw_rows: int = 0
    rows: int = 0
    time_basis: str = "unknown"  # absolute / relative_day / missing
    renamed: Dict[str, str] = field(default_factory=dict)
    unrecognized: List[str] = field(default_factory=list)
    missing_optional: List[str] = field(default_factory=list)
    duplicate_rows: int = 0
    inconsistent_clicks: int = 0
    inconsistent_conversions: int = 0
    negative_cost_rows: int = 0

    @property
    def duplicate_rate(self) -> float:
        return (self.duplicate_rows / self.raw_rows) if self.raw_rows else 0.0

    def to_dict(self) -> Dict[str, Any]:
        payload = dict(self.__dict__)
        payload["duplicate_rate"] = self.duplicate_rate
        return payload


def resolve_layout(frame: pd.DataFrame, declared: str = "auto") -> str:
    """判定输入布局；``declared`` 非 ``auto`` 时以显式声明为准。

    自动判定在「既有 0/1 的 click 标记、又有 impressions 计数列」时容易误判，
    所以补一条数据校验：click 全为 0/1 且 impressions 恒等于 1 时仍按曝光级处理。
    """
    if declared not in {"auto", "impression", "aggregated"}:
        raise ValueError(f"未知 layout：{declared}（应为 auto / impression / aggregated）")
    if declared != "auto":
        return declared

    layout = detect_layout(frame.columns)
    lowered = {str(c).strip().lower(): c for c in frame.columns}
    if "click" in lowered and "impressions" in lowered:
        click = pd.to_numeric(frame[lowered["click"]], errors="coerce").dropna()
        impressions = pd.to_numeric(frame[lowered["impressions"]], errors="coerce").dropna()
        if (len(click) and click.between(0, 1).all()
                and len(impressions) and bool((impressions == 1).all())):
            return "impression"
    return layout


def _numeric(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    """取一列并转数值；缺列返回常量列。"""
    if column not in frame.columns:
        return pd.Series([default] * len(frame), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(default).astype("float64")


def _impression_to_counts(renamed: pd.DataFrame, report: NormalizeReport) -> pd.DataFrame:
    """曝光级日志 → 计数表：每行记一次曝光，click / conversion 作为 0/1 计数。"""
    working = pd.DataFrame(index=renamed.index)
    working["impressions"] = 1.0
    for canonical, source_hint in (("clicks", "点击标记"), ("conversions", "转化标记")):
        if canonical in renamed.columns:
            working[canonical] = _numeric(renamed, canonical).clip(lower=0.0, upper=1.0)
        else:
            working[canonical] = 0.0
            report.missing_optional.append(f"{canonical}（{source_hint}缺失，按 0 计）")
    working["cost"] = _numeric(renamed, "cost")
    if "attributed_conversions" in renamed.columns:
        working["attributed_conversions"] = pd.to_numeric(
            renamed["attributed_conversions"], errors="coerce"
        )
    for column in ("date", "hour") + CONTEXT_COLUMNS + PASSTHROUGH_COLUMNS:
        if column in renamed.columns:
            working[column] = renamed[column].values
    return working


def _coerce_counts(renamed: pd.DataFrame, report: NormalizeReport) -> pd.DataFrame:
    """聚合报表路径：保留全部维度列，只做类型收敛。"""
    working = renamed.copy()
    for column in COUNT_COLUMNS:
        if column in working.columns:
            working[column] = pd.to_numeric(working[column], errors="coerce")
    for column in ("date", "hour") + CONTEXT_COLUMNS + PASSTHROUGH_COLUMNS:
        if column not in working.columns:
            report.missing_optional.append(column)
    if "conversions" not in working.columns:
        working["conversions"] = 0.0
        report.missing_optional.append("conversions")
    if "cost" not in working.columns:
        working["cost"] = 0.0
        report.missing_optional.append("cost")
    return working


def _derive_time(frame: pd.DataFrame, report: NormalizeReport) -> pd.DataFrame:
    """归一化时间维度：绝对日期 / 相对天数 / 仅小时 / 缺失。"""
    date_values = frame["date"] if "date" in frame.columns else None
    hour_values = frame["hour"] if "hour" in frame.columns else None

    if date_values is None or date_values.isna().all():
        report.time_basis = "missing"
        frame["date"] = pd.NA
    else:
        numeric = pd.to_numeric(date_values, errors="coerce")
        numeric_ratio = float(numeric.notna().mean()) if len(numeric) else 0.0
        if numeric_ratio > 0.9:
            maximum = numeric.max(skipna=True)
            if maximum is not None and float(maximum) >= _EPOCH_FLOOR:
                parsed = pd.to_datetime(numeric, unit="s", errors="coerce", utc=True)
                frame["date"] = parsed.dt.strftime("%Y-%m-%d")
                if hour_values is None:
                    frame["hour"] = parsed.dt.hour.map(lambda v: None if pd.isna(v) else int(v))
                report.time_basis = "absolute"
            else:
                # 相对秒数（公开数据集常见）：只能还原到「第 N 天 + 小时」
                day_index = (numeric // _SECONDS_PER_DAY)
                frame["date"] = [
                    None if pd.isna(v) else "D%03d" % (int(v) + 1) for v in day_index
                ]
                if hour_values is None:
                    hour = (numeric % _SECONDS_PER_DAY) // 3600
                    frame["hour"] = [None if pd.isna(v) else int(v) for v in hour]
                report.time_basis = "relative_day"
        else:
            parsed = pd.to_datetime(date_values, errors="coerce", format="mixed")
            if parsed.notna().any():
                frame["date"] = parsed.dt.strftime("%Y-%m-%d")
                if hour_values is None:
                    frame["hour"] = parsed.dt.hour.map(lambda v: None if pd.isna(v) else int(v))
                report.time_basis = "absolute"
            elif date_values.dropna().astype(str).str.fullmatch(r"D\d+").all():
                # 已经是相对天序号（例如前一次流水线导出的中间产物）：沿用并如实标注
                frame["date"] = date_values.astype("string")
                report.time_basis = "relative_day"
            else:
                frame["date"] = date_values.astype("string")
                report.time_basis = "unknown"

    if hour_values is not None:
        hours = pd.to_numeric(hour_values, errors="coerce")
        frame["hour"] = [None if pd.isna(v) else int(v) % 24 for v in hours]
    return frame


def normalize(
    frame: pd.DataFrame,
    *,
    layout: str = "auto",
) -> Tuple[pd.DataFrame, NormalizeReport]:
    """把原始投放表清洗成统一计数表。

    Args:
        frame: 原始表（列名任意，来自任意投放后台导出）。
        layout: ``auto`` / ``impression`` / ``aggregated``。

    Returns:
        ``(clean_frame, report)``。计数器为 ``float64``；无法识别的来源列不会
        进入返回表（列名记在 ``report.unrecognized``）。
    """
    if not len(frame):
        raise ValueError("清洗失败：输入表为空")

    report = NormalizeReport(raw_rows=int(len(frame)))
    rename_map, unmapped = map_columns(frame.columns)
    report.renamed = rename_map
    report.unrecognized = list(unmapped)

    renamed = frame.rename(columns=rename_map)
    renamed = renamed.loc[:, ~renamed.columns.duplicated()]

    report.layout = resolve_layout(frame, layout)
    working = (
        _impression_to_counts(renamed, report)
        if report.layout == "impression"
        else _coerce_counts(renamed, report)
    )

    if "impressions" not in working.columns or "clicks" not in working.columns:
        raise ValueError(
            "清洗失败：缺少曝光量或点击量字段（已识别的列："
            f"{sorted(rename_map.values())}）。可用 --layout 显式指定布局。"
        )

    working = _derive_time(working, report)

    # ---- 去重与一致性检查（只记录，不静默篡改）----
    # 去重必须覆盖**全部列**（含 user_id）：同一时刻同一计划的两条曝光若 uid 不同，
    # 就是两次真实曝光，不是重复数据；把 user_id 排除在外会制造假重复率。
    before = len(working)
    working = working.drop_duplicates().reset_index(drop=True)
    report.duplicate_rows = before - len(working)

    working["cost"] = pd.to_numeric(working["cost"], errors="coerce")
    negative = working["cost"] < 0
    report.negative_cost_rows = int(negative.sum())
    working.loc[negative, "cost"] = None

    for column in COUNT_COLUMNS:
        if column in working.columns:
            working[column] = pd.to_numeric(working[column], errors="coerce")
    for column in ("impressions", "clicks", "conversions", "cost"):
        working[column] = working[column].fillna(0.0)

    inconsistent_clicks = working["clicks"] > working["impressions"]
    report.inconsistent_clicks = int(inconsistent_clicks.sum())
    working.loc[inconsistent_clicks, "clicks"] = working.loc[inconsistent_clicks, "impressions"]

    inconsistent_conversions = working["conversions"] > working["clicks"]
    report.inconsistent_conversions = int(inconsistent_conversions.sum())
    working.loc[inconsistent_conversions, "conversions"] = working.loc[
        inconsistent_conversions, "clicks"
    ]

    keep = [c for c in KEEP_COLUMNS if c in working.columns]
    working = working[keep].reset_index(drop=True)
    report.rows = int(len(working))
    return working, report


def write_tidy(frame: pd.DataFrame, path: str | Path) -> Path:
    """把统一计数表写出为 CSV（utf-8-sig，便于 Excel 直接打开）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def latest_period(frame: pd.DataFrame) -> Optional[str]:
    """返回最新日期（字符串）；无日期列返回 None。"""
    if "date" not in frame.columns:
        return None
    values = frame["date"].dropna()
    if not len(values):
        return None
    return str(sorted({str(v) for v in values.unique()})[-1])
