# -*- coding: utf-8 -*-
"""指标层：投放指标与计费口径换算的纯函数实现。

设计原则（沿用「口径必须可复算」的约定）：

- 每个指标一个纯函数（输入标量、输出 ``float | None``），除零 / 缺值一律返回
  ``None``，绝不抛出——便于单测，也避免某个计划没转化把整份报告打断；
- 计费口径换算（CPM / CPC / CPA / oCPC 出价）单独成组，因为这是投放里最容易
  说错的一环：``eCPM = CTR × CVR × 目标CPA × 1000`` 这条恒等式必须能被验证。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd

Number = Optional[float]


def _is_missing(value: Any) -> bool:
    """判断是否为缺失值（None / NaN / 非数值）。"""
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def safe_div(numerator: Any, denominator: Any) -> Number:
    """安全除法：任一为空 / 分母为 0 → ``None``。"""
    if _is_missing(numerator) or _is_missing(denominator):
        return None
    denominator = float(denominator)
    if denominator == 0:
        return None
    return float(numerator) / denominator


# ---------------------------------------------------------------------------
# 一、基础效率指标
# ---------------------------------------------------------------------------


def ctr(clicks: Any, impressions: Any) -> Number:
    """点击率 CTR = 点击 / 曝光。"""
    return safe_div(clicks, impressions)


def cvr(conversions: Any, clicks: Any) -> Number:
    """点击转化率 CVR = 转化 / 点击。"""
    return safe_div(conversions, clicks)


def ipv(conversions: Any, impressions: Any) -> Number:
    """曝光转化率（转化 / 曝光）。漏斗口径用，避免只看 CVR 忽略点击环节。"""
    return safe_div(conversions, impressions)


def cpc(cost: Any, clicks: Any) -> Number:
    """单次点击成本 CPC = 消耗 / 点击。"""
    return safe_div(cost, clicks)


def cpm(cost: Any, impressions: Any) -> Number:
    """千次曝光成本 CPM = 消耗 / 曝光 × 1000。"""
    value = safe_div(cost, impressions)
    return None if value is None else value * 1000.0


def cpa(cost: Any, conversions: Any) -> Number:
    """单次转化成本 CPA = 消耗 / 转化。"""
    return safe_div(cost, conversions)


def cost_per_click_equivalent(cost: Any, clicks: Any) -> Number:
    """CPC 的别名入口（保留给报表口径对照，避免调用方各写一遍）。"""
    return cpc(cost, clicks)


def attributed_rate(attributed: Any, conversions: Any) -> Number:
    """归因覆盖率 = 被平台归因的转化 / 全部转化。

    低于阈值通常不是「效果不好」，而是归因窗口 / 回传链路的问题——
    这类问题必须先排掉，否则后面所有 CPA 都不可信。
    """
    return safe_div(attributed, conversions)


def click_rate_of_conversions(clicks: Any, conversions: Any) -> Number:
    """点击转化比的倒数口径（1 转化需要几次点击），运营口语里的「转化成本效率」。"""
    return safe_div(clicks, conversions)


# ---------------------------------------------------------------------------
# 二、计费口径换算（CPM / CPC / CPA / oCPC）
# ---------------------------------------------------------------------------


def cpm_from_cpc(cpc_value: Any, ctr_value: Any) -> Number:
    """由 CPC 与 CTR 推 CPM：``CPM = CPC × CTR × 1000``。"""
    if _is_missing(cpc_value) or _is_missing(ctr_value):
        return None
    return float(cpc_value) * float(ctr_value) * 1000.0


def cpc_from_cpm(cpm_value: Any, ctr_value: Any) -> Number:
    """由 CPM 与 CTR 反推 CPC：``CPC = CPM / (CTR × 1000)``。"""
    if _is_missing(cpm_value) or _is_missing(ctr_value) or float(ctr_value) == 0:
        return None
    return float(cpm_value) / (float(ctr_value) * 1000.0)


def cpa_from_cpc(cpc_value: Any, cvr_value: Any) -> Number:
    """由 CPC 与 CVR 推 CPA：``CPA = CPC / CVR``。"""
    if _is_missing(cpc_value) or _is_missing(cvr_value) or float(cvr_value) == 0:
        return None
    return float(cpc_value) / float(cvr_value)


def ecpm_from_funnel(ctr_value: Any, cvr_value: Any, target_cpa: Any) -> Number:
    """oCPC 出价的媒体侧等价千次曝光收入：``eCPM = CTR × CVR × 目标CPA × 1000``。

    这条恒等式是「按转化出价」与「按曝光卖量」之间的换算桥：媒体侧只关心
    eCPM，广告主只关心 CPA，中间两层转化率就是议价空间。
    """
    if _is_missing(ctr_value) or _is_missing(cvr_value) or _is_missing(target_cpa):
        return None
    return float(ctr_value) * float(cvr_value) * float(target_cpa) * 1000.0


def target_cpa_from_ecpm(ecpm_value: Any, ctr_value: Any, cvr_value: Any) -> Number:
    """反向换算：给定媒体侧 eCPM 与两层转化率，反推可承受的目标 CPA。"""
    if _is_missing(ecpm_value) or _is_missing(ctr_value) or _is_missing(cvr_value):
        return None
    denominator = float(ctr_value) * float(cvr_value) * 1000.0
    if denominator == 0:
        return None
    return float(ecpm_value) / denominator


def bid_consistency_gap(real_cpm: Any, implied_cpm: Any) -> Number:
    """实际 CPM 与「按目标 CPA 反推出的 CPM」的相对偏离。

    用于诊断「计费口径不一致」：偏离过大说明消耗口径、转化回传或出价方式
    三者至少有一个和报表假设不同。返回 ``None`` 表示无法判定。
    """
    if _is_missing(real_cpm) or _is_missing(implied_cpm):
        return None
    base = float(implied_cpm)
    if base == 0:
        return None
    return (float(real_cpm) - base) / base


# ---------------------------------------------------------------------------
# 三、结构指标：集中度 / 长尾 / 稳定性
# ---------------------------------------------------------------------------


def hhi(values: Sequence[Any]) -> Number:
    """赫芬达尔指数（各计划消耗占比的平方和）。

    ``1/n`` 表示完全平均，越接近 1 越集中。投放里超过 0.25 通常意味着
    消耗过度押注在少数计划上，一次素材衰退就会让整体成本失控。
    """
    clean = [float(v) for v in values if not _is_missing(v) and float(v) > 0]
    total = sum(clean)
    if not clean or total <= 0:
        return None
    return sum((v / total) ** 2 for v in clean)


def top_k_share(values: Sequence[Any], k: int) -> Number:
    """前 K 项的占比（长尾/头部集中度）。"""
    clean = sorted((float(v) for v in values if not _is_missing(v) and float(v) > 0), reverse=True)
    total = sum(clean)
    if not clean or total <= 0 or k <= 0:
        return None
    return sum(clean[:k]) / total


def gini(values: Sequence[Any]) -> Number:
    """基尼系数（消耗分布不均衡度），0 为完全平均。

    比 HHI 更敏感于长尾：HHI 看头部，基尼看整体不均衡。
    """
    clean = sorted(float(v) for v in values if not _is_missing(v) and float(v) >= 0)
    n = len(clean)
    total = sum(clean)
    if n == 0 or total <= 0:
        return None
    cumulative = sum((i + 1) * v for i, v in enumerate(clean))
    return (2.0 * cumulative) / (n * total) - (n + 1.0) / n


def coefficient_of_variation(values: Sequence[Any]) -> Number:
    """变异系数（标准差 / 均值），衡量指标在时间或维度上的波动。

    消耗节奏异常、CTR 忽高忽低都会在这里体现；均值为 0 或无有效值时返回 None。
    """
    clean = [float(v) for v in values if not _is_missing(v)]
    if len(clean) < 2:
        return None
    mean = sum(clean) / len(clean)
    if mean == 0:
        return None
    variance = sum((v - mean) ** 2 for v in clean) / (len(clean) - 1)
    return (variance ** 0.5) / abs(mean)


def zscore(value: Any, values: Sequence[Any]) -> Number:
    """某值在其分布中的标准分（用于单日消耗偏离告警）。"""
    clean = [float(v) for v in values if not _is_missing(v)]
    if len(clean) < 3 or _is_missing(value):
        return None
    mean = sum(clean) / len(clean)
    variance = sum((v - mean) ** 2 for v in clean) / (len(clean) - 1)
    std = variance ** 0.5
    if std == 0:
        return None
    return (float(value) - mean) / std


def relative_change(base: Any, current: Any) -> Number:
    """相对变化率 ``(current - base) / base``；基期为 0 或缺失时返回 None。"""
    if _is_missing(base) or _is_missing(current) or float(base) == 0:
        return None
    return (float(current) - float(base)) / float(base)


# ---------------------------------------------------------------------------
# 四、指标汇总（供图表层与成稿层消费）
# ---------------------------------------------------------------------------

#: 指标定义：code → (中文标签, 格式化方式, 说明)
INDICATOR_SPECS: List[Dict[str, str]] = [
    {"code": "impressions", "label": "曝光量", "fmt": "int", "hint": "投放日志中的曝光条数"},
    {"code": "clicks", "label": "点击量", "fmt": "int", "hint": "click = 1 的曝光条数"},
    {"code": "conversions", "label": "转化数", "fmt": "int", "hint": "conversion = 1 的曝光条数"},
    {"code": "cost", "label": "总消耗", "fmt": "money", "hint": "所有曝光成本合计"},
    {"code": "ctr", "label": "CTR", "fmt": "pct", "hint": "点击 / 曝光"},
    {"code": "cvr", "label": "CVR", "fmt": "pct", "hint": "转化 / 点击"},
    {"code": "ipv", "label": "曝光转化率", "fmt": "pct", "hint": "转化 / 曝光（漏斗全链路）"},
    {"code": "cpc", "label": "CPC", "fmt": "money4", "hint": "消耗 / 点击"},
    {"code": "cpm", "label": "CPM", "fmt": "money4", "hint": "消耗 / 曝光 × 1000"},
    {"code": "cpa", "label": "CPA", "fmt": "money4", "hint": "消耗 / 转化"},
    {"code": "attributed_rate", "label": "归因覆盖率", "fmt": "pct", "hint": "归因转化 / 全部转化"},
    {"code": "cost_per_conv_click", "label": "单转化点击数", "fmt": "num2", "hint": "点击 / 转化，越低说明转化环节越顺"},
]

FUNNEL_SPECS: List[Dict[str, str]] = [
    {"code": "impressions", "label": "曝光", "fmt": "int"},
    {"code": "clicks", "label": "点击", "fmt": "int"},
    {"code": "conversions", "label": "转化", "fmt": "int"},
]


def _fmt(value: Number, fmt: str) -> str:
    if value is None:
        return "—"
    if fmt == "pct":
        return f"{value * 100:.2f}%"
    if fmt == "num2":
        return f"{value:.2f}"
    if fmt == "int":
        return f"{int(round(value)):,}"
    if fmt == "money":
        return f"{value:,.2f}"
    if fmt == "money4":
        return f"{value:,.4f}"
    return str(value)


def build_metrics(totals: Mapping[str, Any]) -> Dict[str, Any]:
    """组装 KPI 汇总。

    Args:
        totals: 至少包含 ``impressions`` / ``clicks`` / ``conversions`` / ``cost``
            的聚合字典（可含 ``attributed_conversions``）。

    Returns:
        结构化字典::

            {
              "totals": {...},                  # 原始计数
              "derived": {code: float|None},    # 全部派生指标
              "indicators": [{code,label,value,display,unit,hint}, ...],
              "funnel": [{code,label,value,display,step_rate}, ...],
            }
    """
    impressions = totals.get("impressions")
    clicks = totals.get("clicks")
    conversions = totals.get("conversions")
    cost = totals.get("cost")
    attributed = totals.get("attributed_conversions")

    derived = {
        "ctr": ctr(clicks, impressions),
        "cvr": cvr(conversions, clicks),
        "ipv": ipv(conversions, impressions),
        "cpc": cpc(cost, clicks),
        "cpm": cpm(cost, impressions),
        "cpa": cpa(cost, conversions),
        "attributed_rate": attributed_rate(attributed, conversions) if attributed is not None else None,
        "cost_per_conv_click": click_rate_of_conversions(clicks, conversions),
    }

    values = {
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "cost": cost,
        **derived,
    }

    indicators = [
        {
            "code": spec["code"],
            "label": spec["label"],
            "value": None if _is_missing(values.get(spec["code"])) else values.get(spec["code"]),
            "display": _fmt(values.get(spec["code"]), spec["fmt"]),
            "unit": {"pct": "%", "money": "元", "money4": "元", "int": "次"}.get(spec["fmt"], ""),
            "hint": spec["hint"],
        }
        for spec in INDICATOR_SPECS
    ]

    funnel = []
    previous: Number = None
    for spec in FUNNEL_SPECS:
        value = values.get(spec["code"])
        step_rate = None if previous is None else safe_div(value, previous)
        funnel.append({
            "code": spec["code"],
            "label": spec["label"],
            "value": value,
            "display": _fmt(value, spec["fmt"]),
            "step_rate": step_rate,
            "step_display": _fmt(step_rate, "pct"),
        })
        previous = value

    return {
        "totals": {k: (None if _is_missing(v) else float(v)) for k, v in totals.items()},
        "derived": derived,
        "indicators": indicators,
        "funnel": funnel,
    }


def cost_concentration(cost_by_campaign: Mapping[str, Any]) -> Dict[str, Number]:
    """消耗集中度三项指标（HHI / 前 3 占比 / 基尼）。"""
    values = list(cost_by_campaign.values())
    return {
        "hhi": hhi(values),
        "top3_share": top_k_share(values, 3),
        "top1_share": top_k_share(values, 1),
        "gini": gini(values),
    }


def reallocate_budget(
    campaigns: Iterable[Mapping[str, Any]],
    *,
    total_budget: Number = None,
    tolerance: float = 1.0,
) -> List[Dict[str, Any]]:
    """确定性预算再分配建议。

    规则：在「有足够转化样本」的计划里算 CPA，把高于容差倍数中位 CPA 的计划
    按超出比例削减，削减额按 CPA 倒数加权分配给效率更好的计划。纯函数，
    同输入同输出——建议可以被逐条复算，不是拍脑袋。

    Args:
        campaigns: 每项需含 ``campaign_id`` / ``cost`` / ``conversions``。
        total_budget: 预算总额；None 时取所有计划消耗之和（即「预算不变的重分配」）。
        tolerance: 允许的 CPA 相对偏离（1.0 表示只削高于中位数 1 倍以上的）。

    Returns:
        ``[{campaign_id, cpa, cost, delta, reason}, ...]``，按 ``delta`` 升序。
    """
    rows = []
    for row in campaigns:
        cost = row.get("cost")
        conversions = row.get("conversions")
        cpa_value = cpa(cost, conversions)
        rows.append({
            "campaign_id": row.get("campaign_id"),
            "cost": None if _is_missing(cost) else float(cost),
            "conversions": None if _is_missing(conversions) else float(conversions),
            "cpa": cpa_value,
        })

    efficient = [r for r in rows if r["cpa"] is not None and (r["conversions"] or 0) > 0]
    if len(efficient) < 2:
        return []

    ordered = sorted(r["cpa"] for r in efficient)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    if median <= 0:
        return []

    budget = float(total_budget) if total_budget is not None else sum((r["cost"] or 0.0) for r in rows)
    pool = 0.0
    for row in efficient:
        if row["cpa"] > median * (1.0 + tolerance):
            cut_ratio = min(0.5, (row["cpa"] - median) / (row["cpa"] * 1.0) if row["cpa"] else 0.0)
            cut = (row["cost"] or 0.0) * cut_ratio
            row["delta"] = -cut
            pool += cut
        elif row["cpa"] < median:
            row["delta"] = 0.0
        else:
            row["delta"] = 0.0

    gainers = [r for r in efficient if r["cpa"] < median]
    weight_total = sum(1.0 / r["cpa"] for r in gainers) or 1.0
    for row in gainers:
        row["delta"] = pool * (1.0 / row["cpa"]) / weight_total

    for row in rows:
        row.setdefault("delta", 0.0)
        if row["delta"] < 0:
            row["reason"] = f"CPA {row['cpa']:,.4f} 高于中位 {median:,.4f}，按超出比例削减"
        elif row["delta"] > 0:
            row["reason"] = f"CPA {row['cpa']:,.4f} 低于中位，承接削减预算"
        else:
            row["reason"] = "维持不变" if row["cpa"] is not None else "转化样本不足，本次不调整"

    return sorted(rows, key=lambda r: r["delta"])
