# -*- coding: utf-8 -*-
"""诊断发现的排序与汇总。

优先级公式（可复算，不是拍脑袋）::

    priority = 级别权重 × (0.3 + 0.7 × 影响消耗占比)

含义：一个「高」级别但只影响 1% 消耗的问题，排在一个「中」级别但影响 40%
消耗的问题后面——投放复盘的时间永远有限，先修钱多的。
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from ..util import clamp

#: 级别 → 权重
LEVEL_WEIGHT: Dict[str, float] = {"high": 3.0, "medium": 2.0, "low": 1.0}

#: 级别 → 中文标签
LEVEL_LABEL: Dict[str, str] = {"high": "高", "medium": "中", "low": "低"}

#: 影响占比的可信上限（占比本身不会超过 1，这里只是防御异常输入）
_SHARE_CEILING = 1.0

#: 占比部分的基准权重（保证「影响极小但级别高」的问题仍然排在前列）
_SHARE_FLOOR = 0.3


def priority_score(level: str, spend_share: float = 0.0) -> float:
    """计算优先级分数（保留 4 位小数，便于测试断言）。

    Args:
        level: ``high`` / ``medium`` / ``low``；未知级别按 ``low`` 处理。
        spend_share: 该问题影响的消耗占比（0–1）。
    """
    weight = LEVEL_WEIGHT.get(str(level).lower(), 1.0)
    share = clamp(float(spend_share or 0.0), 0.0, _SHARE_CEILING)
    return round(weight * (_SHARE_FLOOR + 0.7 * share), 4)


def rank_findings(findings: Sequence[Any]) -> List[Any]:
    """按优先级降序排列，并把分数写回每个发现对象。

    同分时按级别权重、再按规则编号排序——保证同一份输入永远得到同一个顺序，
    报告 diff 才有意义。
    """
    for finding in findings:
        finding.priority = priority_score(finding.level, getattr(finding, "spend_share", 0.0))
    return sorted(
        findings,
        key=lambda f: (-f.priority, -LEVEL_WEIGHT.get(f.level, 1.0), f.rule_id),
    )


def summarize(findings: Sequence[Any]) -> Dict[str, Any]:
    """统计各级别问题数量与受影响消耗占比。

    占比可能**重叠**（一个计划既可能触发 R03 又可能触发 R07），所以这里同时
    给出原始合计与截顶值：报告里用截顶值讲「影响面」，用 ``overlapping`` 标记
    提醒读者不要把这几个百分比相加。
    """
    counts = {"high": 0, "medium": 0, "low": 0}
    for finding in findings:
        counts[finding.level] = counts.get(finding.level, 0) + 1
    raw_share = sum(getattr(f, "spend_share", 0.0) or 0.0 for f in findings)
    return {
        "total": len(findings),
        "counts": counts,
        "affected_spend_share": round(clamp(raw_share, 0.0, 1.0), 4),
        "affected_spend_share_raw": round(raw_share, 4),
        "overlapping": raw_share > 1.0,
        "top": findings[0].rule_id if findings else None,
    }
