# -*- coding: utf-8 -*-
"""统计检验：只用标准库实现，保证「同一输入同一结论」且可离线复算。

为什么不用 scipy：投放复盘里的检验只有两比例检验与置信区间这几种，
自己实现有三个好处——(1) 依赖少，任何环境零安装可跑；(2) 公式写在代码里，
结论能被逐行复核，而不是「调了个包黑箱出个 p 值」；(3) 数值口径可由我们
钉死（例如检验用合并方差、区间用非合并方差），不会随库版本漂移。

术语对照（报告里也用这套词，避免各说各话）：

- ``x1/n1`` 对照组（baseline），``x2/n2`` 实验组（variant）
- ``alpha`` 显著性水平，``power`` 功效（1 − β）
- ``mde`` 最小可检出差异（Minimum Detectable Effect）
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any, Dict, List, Optional

_NORMAL = NormalDist()


def norm_cdf(z: float) -> float:
    """标准正态累积分布函数。"""
    return _NORMAL.cdf(z)


def norm_ppf(p: float) -> float:
    """标准正态分位数；``p`` 越界时抛 ``ValueError``。"""
    if not 0.0 < p < 1.0:
        raise ValueError(f"分位数要求 0 < p < 1，收到 {p}")
    return _NORMAL.inv_cdf(p)


def z_for(alpha: float, *, two_sided: bool = True) -> float:
    """由显著性水平取临界 z 值（默认双侧）。"""
    if two_sided:
        return norm_ppf(1.0 - alpha / 2.0)
    return norm_ppf(1.0 - alpha)


# ---------------------------------------------------------------------------
# 一、两比例检验
# ---------------------------------------------------------------------------


def two_proportion_z_test(
    x1: float, n1: float, x2: float, n2: float, *, alpha: float = 0.05
) -> Dict[str, Any]:
    """两比例 z 检验（双侧）+ 差异置信区间。

    检验用**合并方差**（原假设：两组同率），区间用**非合并方差**（对真实差异
    的区间估计）——这是标准做法，混用会让区间偏窄、结论偏乐观。

    Returns:
        ``{p1, p2, diff, relative_lift, z, p_value, ci_low, ci_high, significant, alpha, note}``
    """
    result: Dict[str, Any] = {
        "x1": float(x1), "n1": float(n1), "x2": float(x2), "n2": float(n2),
        "alpha": float(alpha),
        "p1": None, "p2": None, "diff": None, "relative_lift": None,
        "z": None, "p_value": None, "ci_low": None, "ci_high": None,
        "significant": None, "note": "",
    }
    if n1 <= 0 or n2 <= 0:
        result["note"] = "样本量为 0，无法检验"
        return result

    p1 = float(x1) / float(n1)
    p2 = float(x2) / float(n2)
    result["p1"], result["p2"] = p1, p2
    result["diff"] = p2 - p1
    result["relative_lift"] = (p2 - p1) / p1 if p1 > 0 else None

    pooled = (float(x1) + float(x2)) / (float(n1) + float(n2))
    if pooled in (0.0, 1.0):
        result["note"] = "事件率为 0 或 1，正态近似失效"
        return result

    se_pooled = math.sqrt(pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2))
    if se_pooled == 0:
        result["note"] = "合并标准误为 0"
        return result
    z = (p2 - p1) / se_pooled
    result["z"] = z
    result["p_value"] = 2.0 * (1.0 - norm_cdf(abs(z)))

    se_diff = math.sqrt(p1 * (1.0 - p1) / n1 + p2 * (1.0 - p2) / n2)
    critical = z_for(alpha)
    result["ci_low"] = (p2 - p1) - critical * se_diff
    result["ci_high"] = (p2 - p1) + critical * se_diff
    result["significant"] = bool(result["p_value"] < alpha)
    return result


def chi_square_2x2(x1: float, n1: float, x2: float, n2: float) -> Dict[str, Any]:
    """2×2 列联表的卡方检验。

    单自由度下 ``chi2 = z²``，因此这里复用两比例检验的合并方差 z。保留独立
    入口是因为投放复盘里分析师常按卡方口径对表，需要能互相校验。
    """
    test = two_proportion_z_test(x1, n1, x2, n2)
    if test["z"] is None:
        return {"chi2": None, "p_value": None, "note": test["note"] or "无法计算"}
    return {
        "chi2": test["z"] ** 2,
        "p_value": test["p_value"],
        "note": "单自由度：chi2 = z²",
    }


def wilson_interval(x: float, n: float, *, alpha: float = 0.05) -> Dict[str, Optional[float]]:
    """比例的 Wilson 置信区间。

    比正态近似区间更适合小样本与极端比例（CTR 常在 1% 以下），
    不会算出负数下界——报表里出现负 CTR 区间是最容易被质疑的细节。
    """
    if n <= 0:
        return {"p": None, "low": None, "high": None}
    p = float(x) / float(n)
    z = z_for(alpha)
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denominator
    half = (z / denominator) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return {"p": p, "low": max(0.0, centre - half), "high": min(1.0, centre + half)}


# ---------------------------------------------------------------------------
# 二、实验设计：样本量 / 功效 / MDE
# ---------------------------------------------------------------------------


def sample_size_per_arm(
    baseline_rate: float,
    mde_abs: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> Optional[float]:
    """每组所需曝光（或点击）样本量。

    标准两比例样本量公式::

        n = (z_{1-α/2} + z_{power})² × [p1(1-p1) + p2(1-p2)] / (p1 - p2)²

    Args:
        baseline_rate: 对照组基准率（如 CTR = 0.008）。
        mde_abs: 希望检出的**绝对**差异（如 0.001 表示 0.1 个百分点）。

    Returns:
        每组最小样本量（向上取整）；参数非法时返回 None。
    """
    if not 0.0 < baseline_rate < 1.0 or mde_abs <= 0:
        return None
    p1 = baseline_rate
    p2 = baseline_rate + mde_abs
    if not 0.0 < p2 < 1.0:
        return None
    z_alpha = z_for(alpha)
    z_beta = norm_ppf(power)
    numerator = (z_alpha + z_beta) ** 2 * (p1 * (1.0 - p1) + p2 * (1.0 - p2))
    return math.ceil(numerator / (p2 - p1) ** 2)


def power_for_effect(
    baseline_rate: float,
    mde_abs: float,
    n_per_arm: float,
    *,
    alpha: float = 0.05,
) -> Optional[float]:
    """给定样本量与效应量时的检验功效。

    采用标准两比例检验的功效公式：原假设下用**合并方差**（两组同率），
    备择假设下用各自方差。这样 ``power_for_effect`` 与
    :func:`sample_size_per_arm` 互为逆运算，可以用「样本量公式 → 反查功效 ≈ 目标功效」
    来互相校验。
    """
    if n_per_arm <= 0 or not 0.0 < baseline_rate < 1.0 or mde_abs == 0:
        return None
    p1 = baseline_rate
    p2 = baseline_rate + mde_abs
    if not 0.0 < p2 < 1.0:
        return None
    pooled = (p1 + p2) / 2.0
    se_null = math.sqrt(pooled * (1.0 - pooled) * 2.0 / n_per_arm)
    se_alt = math.sqrt(p1 * (1.0 - p1) / n_per_arm + p2 * (1.0 - p2) / n_per_arm)
    if se_alt == 0:
        return None
    z_alpha = z_for(alpha)
    z = (abs(p2 - p1) - z_alpha * se_null) / se_alt
    return min(1.0, max(0.0, norm_cdf(z)))


def mde_for_sample(
    baseline_rate: float,
    n_per_arm: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
    tolerance: float = 1e-7,
) -> Optional[float]:
    """给定样本量反解最小可检出**绝对**差异。

    二分求解： ``power_for_effect`` 关于 ``mde`` 单调递增，因此可以直接二分；
    比闭式近似稳，也不会在小效应量处给出反直觉结果。
    """
    if n_per_arm <= 0 or not 0.0 < baseline_rate < 1.0:
        return None
    low, high = 1e-9, min(1.0 - baseline_rate - 1e-9, 0.5)
    if power_for_effect(baseline_rate, high, n_per_arm, alpha=alpha) is None:
        return None
    if power_for_effect(baseline_rate, high, n_per_arm, alpha=alpha) < power:
        return None  # 即使检到最大效应也达不到目标功效
    while high - low > tolerance:
        mid = (low + high) / 2.0
        current = power_for_effect(baseline_rate, mid, n_per_arm, alpha=alpha)
        if current is None or current < power:
            low = mid
        else:
            high = mid
    return high


def mde_relative(
    baseline_rate: float,
    n_per_arm: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> Optional[float]:
    """最小可检出**相对**提升（如 0.12 表示 12%）。"""
    absolute = mde_for_sample(baseline_rate, n_per_arm, alpha=alpha, power=power)
    if absolute is None or baseline_rate <= 0:
        return None
    return absolute / baseline_rate


# ---------------------------------------------------------------------------
# 三、多次查看：序贯检验边界
# ---------------------------------------------------------------------------


def obrien_fleming_boundaries(looks: int, *, alpha: float = 0.05) -> List[Dict[str, float]]:
    """O'Brien-Fleming 消耗函数给出的名义显著性边界。

    「边跑边看」是投放优化里最常见的方法论错误：每看一次就按 0.05 判定，
    假阳性率会随查看次数急剧上升（看 5 次实际接近 14%）。O'Brien-Fleming
    让早期查看的边界极严、末期接近名义水平，总 α 仍受控。

    第 k 次查看（共 K 次）的名义 α::

        α_k = 2 × (1 − Φ( z_{α/2} / sqrt(k / K) ))

    Returns:
        ``[{look, information_fraction, alpha, z}]``，按查看次序排列。
    """
    if looks < 1:
        raise ValueError(f"查看次数必须 ≥1，收到 {looks}")
    z_alpha = z_for(alpha)
    out: List[Dict[str, float]] = []
    for k in range(1, looks + 1):
        fraction = k / looks
        z_boundary = z_alpha / math.sqrt(fraction)
        nominal_alpha = 2.0 * (1.0 - norm_cdf(z_boundary))
        out.append({
            "look": float(k),
            "information_fraction": fraction,
            "alpha": nominal_alpha,
            "z": z_boundary,
        })
    return out


def sequential_check(
    x1: float, n1: float, x2: float, n2: float,
    *, look: int, looks: int, alpha: float = 0.05,
) -> Dict[str, Any]:
    """在第 ``look`` 次查看时按序贯边界判定是否可提前下结论。"""
    boundaries = obrien_fleming_boundaries(looks, alpha=alpha)
    if not 1 <= look <= looks:
        raise ValueError(f"look 必须在 1..{looks} 之间，收到 {look}")
    boundary = boundaries[look - 1]
    test = two_proportion_z_test(x1, n1, x2, n2, alpha=boundary["alpha"] * 0.5)
    return {
        "look": look,
        "looks": looks,
        "nominal_alpha": boundary["alpha"],
        "z_boundary": boundary["z"],
        "z": test["z"],
        "p_value": test["p_value"],
        "stopped": (test["z"] is not None and abs(test["z"]) >= boundary["z"]),
        "note": "达到序贯边界，可提前判定" if (
            test["z"] is not None and abs(test["z"]) >= boundary["z"]
        ) else "未达边界，继续收集数据",
    }
