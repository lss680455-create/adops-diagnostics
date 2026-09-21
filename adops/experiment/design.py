# -*- coding: utf-8 -*-
"""实验设计层：把「这个优化值不值得做实验」变成先算再说的数字。

投放优化的动作几乎都是「换个素材 / 换个出价 / 换个落位」，判断依据不是
感觉而是：**能检出多大的差异、要跑几天、看几次**。这一层输出一个实验方案
草案（假设 / 主指标 / 样本量 / 时长 / 判定边界），可以直接贴进实验平台的
建单页。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .tests_stats import (
    mde_for_sample,
    mde_relative,
    obrien_fleming_boundaries,
    power_for_effect,
    sample_size_per_arm,
    wilson_interval,
)

#: 主指标 → 分母字段（实验的样本量按分母算）
METRIC_DENOMINATOR: Dict[str, str] = {
    "ctr": "impressions",
    "cvr": "clicks",
    "ipv": "impressions",
    "cpa": "conversions",
}

#: 主指标 → 中文名
METRIC_LABEL: Dict[str, str] = {
    "ctr": "点击率 CTR",
    "cvr": "点击转化率 CVR",
    "ipv": "曝光转化率",
    "cpa": "单转化成本 CPA",
}


def design_experiment(
    *,
    metric: str,
    baseline_rate: float,
    daily_denominator: Optional[float] = None,
    alpha: float = 0.05,
    power: float = 0.80,
    mde_rel: float = 0.10,
    looks: int = 1,
    min_days: int = 7,
) -> Dict[str, Any]:
    """产出实验方案草案。

    Args:
        metric: 主指标（``ctr`` / ``cvr`` / ``ipv`` / ``cpa``）。
        baseline_rate: 基准值（CTR 用小数，如 0.008；CPA 用金额比率的倒数口径时请显式转换）。
        daily_denominator: 每日可获得的样本量（曝光量或点击数），用于估算时长。
        alpha / power: 判定阈值与功效。
        mde_rel: 期望检出的**相对**提升（0.10 = 10%）。
        looks: 计划中途查看几次（>1 时启用序贯边界）。
        min_days: 最短运行天数（避免只跑周末或只跑一天就下结论）。

    Returns:
        方案字典：样本量、时长、绝对/相对 MDE、判定边界、风险提示。
    """
    label = METRIC_LABEL.get(metric, metric)
    mde_abs = baseline_rate * mde_rel if baseline_rate > 0 else None
    n_per_arm = sample_size_per_arm(baseline_rate, mde_abs, alpha=alpha, power=power) if mde_abs else None
    total = n_per_arm * 2 if n_per_arm else None

    days = None
    if n_per_arm and daily_denominator:
        days = max(min_days, int(-(-n_per_arm // max(1.0, float(daily_denominator)))))

    achieved_relative_mde = None
    if n_per_arm and daily_denominator and days:
        actual_n = float(daily_denominator) * days
        achieved_relative_mde = mde_relative(baseline_rate, actual_n, alpha=alpha, power=power)

    notes: List[str] = []
    if n_per_arm is None:
        notes.append("基准率或期望提升不合法，无法给出样本量")
    if days is None:
        notes.append("缺少每日样本量，无法估算运行时长（补 --daily 参数）")
    if days is not None and days > 30:
        notes.append(f"需要 {days} 天，超过一个月：建议降低期望提升幅度或不再做此实验")
    if looks > 1:
        boundaries = obrien_fleming_boundaries(looks, alpha=alpha)
        notes.append(
            "计划中途查看 %d 次，已改用 O'Brien-Fleming 边界控制总 α："
            "第 1 次查看的名义 α 仅 %.4f%%" % (looks, boundaries[0]["alpha"] * 100)
        )
    else:
        notes.append("只在下线时判定一次（单次查看），不需要序贯校正")

    payload: Dict[str, Any] = {
        "metric": metric,
        "metric_label": label,
        "denominator": METRIC_DENOMINATOR.get(metric, "impressions"),
        "baseline_rate": baseline_rate,
        "alpha": alpha,
        "power": power,
        "target_relative_lift": mde_rel,
        "mde_absolute": mde_abs,
        "sample_size_per_arm": n_per_arm,
        "sample_size_total": total,
        "daily_denominator": daily_denominator,
        "days_required": days,
        "achievable_relative_mde": achieved_relative_mde,
        "looks": looks,
        "boundaries": obrien_fleming_boundaries(looks, alpha=alpha) if looks > 1 else [
            {"look": 1.0, "information_fraction": 1.0, "alpha": alpha, "z": None}
        ],
        "notes": notes,
    }

    if n_per_arm:
        # 反查：以「预计每日样本量 × 最短天数」为实际规模，给出真正可检出的效应
        if daily_denominator and days:
            payload["verdict"] = (
                f"按每日 {daily_denominator:,.0f} 个{payload['denominator']}、"
                f"跑 {days} 天，可检出约 {achieved_relative_mde * 100:.1f}% 的相对变化"
                if achieved_relative_mde else "样本量充足性无法判定"
            )
        else:
            payload["verdict"] = f"每组需要 {n_per_arm:,} 个{payload['denominator']}"
    else:
        payload["verdict"] = "参数不足，无法给出样本量"
    return payload


def evaluate_existing(
    *,
    metric: str,
    x1: float, n1: float, x2: float, n2: float,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """对已跑完的实验做判定（含区间估计与「结论是否稳」提示）。

    除了 p 值，会额外回答两个实际会被追问的问题：
    「如果真实提升是这个方向，样本量够不够」与「置信区间跨过 0 多少」。
    """
    from .tests_stats import two_proportion_z_test

    test = two_proportion_z_test(x1, n1, x2, n2, alpha=alpha)
    interval_1 = wilson_interval(x1, n1, alpha=alpha)
    interval_2 = wilson_interval(x2, n2, alpha=alpha)

    power_check = None
    if test["p1"] is not None and test["p2"] is not None and test["diff"]:
        power_check = power_for_effect(test["p1"], test["diff"], min(n1, n2), alpha=alpha)

    if test["significant"] is None:
        verdict = "样本不足或事件率退化，无法判定"
    elif test["significant"] and (test["diff"] or 0) > 0:
        verdict = "实验组显著优于对照组，可以放量"
    elif test["significant"]:
        verdict = "实验组显著劣于对照组，应回滚"
    else:
        verdict = "未达显著，不能宣称有效——需要更多样本或差异确实不存在"

    return {
        "metric": metric,
        "metric_label": METRIC_LABEL.get(metric, metric),
        "test": test,
        "control_interval": interval_1,
        "variant_interval": interval_2,
        "post_hoc_power": power_check,
        "verdict": verdict,
        "guardrail": (
            "区间跨 0，不能按点估直接放量"
            if test["ci_low"] is not None and test["ci_low"] <= 0 <= test["ci_high"]
            else "区间未跨 0，方向明确"
        ),
    }
