# -*- coding: utf-8 -*-
"""预算再分配模拟：把「该往哪挪钱」变成一个可复算的确定性算法。

**模型假设必须写在明面上**：假设每个计划在观察期内按其当前 CPA 线性缩放
（多加一块钱就多产出 ``1/CPA`` 个转化）。这是**一阶近似**，真实投放有边际
递减——所以输出的是「值得先试的方向」，不是「照此执行就能省下的钱」。
报告里会把这句假设一起印出来，避免被当成承诺。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from .ads import cpa, safe_div

#: 单计划最大削减比例（防止一次砍掉一半预算导致断流）
MAX_CUT_RATIO = 0.5


def simulate_reallocation(
    campaigns: Iterable[Mapping[str, Any]],
    *,
    tolerance: float = 1.0,
    min_conversions: int = 0,
    max_cut_ratio: float = MAX_CUT_RATIO,
) -> Dict[str, Any]:
    """模拟「预算不变、只调分配」后的整体 CPA。

    Args:
        campaigns: 每项含 ``campaign_id`` / ``cost`` / ``conversions``。
        tolerance: 允许的 CPA 相对偏离（1.0 = 只削高于中位 CPA 一倍以上的）。
        min_conversions: 参与调仓的最低转化样本量（0 表示不设门槛）。
        max_cut_ratio: 单计划最大削减比例。

    Returns:
        ``{moves, moved_cost, cpa_before, cpa_after, improvement, baseline_cpa,
        assumption, note}``；不足以给出建议时 ``moves`` 为空。
    """
    rows: List[Dict[str, Any]] = []
    for row in campaigns:
        conversions = row.get("conversions")
        cost = row.get("cost")
        rows.append({
            "campaign_id": row.get("campaign_id"),
            "cost": float(cost) if cost is not None else 0.0,
            "conversions": float(conversions) if conversions is not None else 0.0,
            "cpa": cpa(cost, conversions),
        })

    total_cost = sum(r["cost"] for r in rows)
    total_conversions = sum(r["conversions"] for r in rows)
    cpa_before = safe_div(total_cost, total_conversions)

    eligible = [
        r for r in rows
        if r["cpa"] is not None and r["conversions"] >= max(min_conversions, 1)
    ]
    if len(eligible) < 3:
        return {
            "moves": [],
            "moved_cost": 0.0,
            "cpa_before": cpa_before,
            "cpa_after": None,
            "improvement": None,
            "baseline_cpa": None,
            "assumption": _ASSUMPTION,
            "note": "参与调仓的计划少于 3 个，样本不足以给出再分配建议",
        }

    ordered = sorted(r["cpa"] for r in eligible)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    if median <= 0:
        return {
            "moves": [], "moved_cost": 0.0, "cpa_before": cpa_before, "cpa_after": None,
            "improvement": None, "baseline_cpa": median, "assumption": _ASSUMPTION,
            "note": "中位 CPA 为 0，无法计算相对效率",
        }

    pool = 0.0
    deltas: Dict[Any, float] = {r["campaign_id"]: 0.0 for r in eligible}
    for row in eligible:
        if row["cpa"] > median * (1.0 + tolerance):
            cut_ratio = min(max_cut_ratio, (row["cpa"] - median) / row["cpa"])
            cut = row["cost"] * cut_ratio
            deltas[row["campaign_id"]] = -cut
            pool += cut

    gainers = [r for r in eligible if r["cpa"] < median]
    if pool <= 0 or not gainers:
        return {
            "moves": [], "moved_cost": 0.0, "cpa_before": cpa_before, "cpa_after": cpa_before,
            "improvement": 0.0, "baseline_cpa": median, "assumption": _ASSUMPTION,
            "note": "所有计划 CPA 均在容差范围内，无需调仓",
        }

    weight_total = sum(1.0 / r["cpa"] for r in gainers)
    for row in gainers:
        deltas[row["campaign_id"]] = pool * (1.0 / row["cpa"]) / weight_total

    # 一阶近似：按当前 CPA 线性外推
    projected_conversions = 0.0
    moves: List[Dict[str, Any]] = []
    for row in eligible:
        delta = deltas[row["campaign_id"]]
        projected_cost = max(0.0, row["cost"] + delta)
        projected_conversions += projected_cost / row["cpa"]
        if abs(delta) > 1e-12:
            moves.append({
                "campaign_id": row["campaign_id"],
                "cost": row["cost"],
                "cpa": row["cpa"],
                "delta": delta,
                "delta_ratio": delta / row["cost"] if row["cost"] else None,
                "reason": (
                    f"CPA {row['cpa']:,.4f} 高于中位 {median:,.4f}，按超出比例削减"
                    if delta < 0 else
                    f"CPA {row['cpa']:,.4f} 低于中位 {median:,.4f}，承接削减预算"
                ),
            })

    # 未参与调仓的计划按原样计入
    for row in rows:
        if row["campaign_id"] not in deltas and row["cpa"] is not None:
            projected_conversions += row["conversions"]

    cpa_after = safe_div(total_cost, projected_conversions)
    improvement = None
    if cpa_before and cpa_after is not None:
        improvement = (cpa_before - cpa_after) / cpa_before

    moves.sort(key=lambda m: m["delta"])
    return {
        "moves": moves,
        "moved_cost": pool,
        "cpa_before": cpa_before,
        "cpa_after": cpa_after,
        "improvement": improvement,
        "baseline_cpa": median,
        "assumption": _ASSUMPTION,
        "note": (
            f"共 {len(moves)} 个计划参与调仓，挪动预算 {pool:,.4f}（预算总额不变）；"
            "该结果是一阶近似，落地时请按每日 10%–20% 的幅度分批执行并观察 3–5 天"
        ),
    }


_ASSUMPTION = (
    "模型假设：各计划在观察期内按当前 CPA 线性缩放。真实投放存在边际递减，"
    "因此结论用于确定「先试哪个方向」，不代表承诺收益。"
)
