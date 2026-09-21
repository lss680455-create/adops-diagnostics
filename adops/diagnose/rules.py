# -*- coding: utf-8 -*-
"""诊断规则引擎：把「投放哪里有问题」变成一组可复算的确定性规则。

为什么用规则而不是让模型直接下结论：

- **可复算**：同一份数据永远得到同一批问题（报告可以 diff，复盘能对上账）；
- **可审计**：每条规则给出触发条件、样本量门槛、实际值，运营可以反驳具体
  数字而不是反驳「AI 觉得」；
- **可解释**：AI 只负责把结论写成通顺的话（见 :mod:`adops.ai`），它不改结论。

每条规则都带**样本量门槛**与（比率类问题）**显著性检验**：小样本下的比率差
是噪声，宁可漏报也不误报——误报的代价是运营对报表失去信任。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from ..config import DiagnoseConfig
from ..experiment.tests_stats import two_proportion_z_test
from ..metrics.ads import relative_change, safe_div, zscore
from ..util import clamp, money, num, pct, ratio, safe_float, signed_pct


@dataclass
class Finding:
    """一条诊断发现。"""

    rule_id: str
    level: str  # high / medium / low
    title: str
    subject: str
    evidence: List[str] = field(default_factory=list)
    action: str = ""
    impact: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    spend_share: float = 0.0
    priority: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "level": self.level,
            "title": self.title,
            "subject": self.subject,
            "evidence": list(self.evidence),
            "action": self.action,
            "impact": self.impact,
            "metrics": dict(self.metrics),
            "spend_share": self.spend_share,
            "priority": self.priority,
        }


@dataclass
class DiagnosisInput:
    """规则引擎的输入快照（全部由上游阶段产出，规则层不再回查原始数据）。"""

    cfg: DiagnoseConfig
    totals: Dict[str, Any]
    metrics: Dict[str, Any]
    campaign_table: pd.DataFrame
    hourly: pd.DataFrame
    daily: pd.DataFrame
    quality: Dict[str, Any]
    concentration: Dict[str, Any]

    @property
    def global_ctr(self) -> Optional[float]:
        return self.metrics.get("derived", {}).get("ctr")

    @property
    def global_cvr(self) -> Optional[float]:
        return self.metrics.get("derived", {}).get("cvr")

    @property
    def global_cpa(self) -> Optional[float]:
        return self.metrics.get("derived", {}).get("cpa")

    @property
    def global_cpm(self) -> Optional[float]:
        return self.metrics.get("derived", {}).get("cpm")

    @property
    def total_cost(self) -> float:
        return safe_float(self.totals.get("cost"))


# ---------------------------------------------------------------------------
# R01 数据质量阻断
# ---------------------------------------------------------------------------


def rule_quality_block(ctx: DiagnosisInput) -> Optional[Finding]:
    """数据质量存在确定性错误时，所有效率结论都不可信，必须先修数据。"""
    failed = [c for c in ctx.quality.get("checks", []) if c["status"] == "fail"]
    if not failed:
        return None
    return Finding(
        rule_id="R01",
        level="high",
        title="数据质量存在确定性错误，本次效率结论不可直接使用",
        subject="整份投放数据",
        evidence=[f"{c['check']}：{c['message']}（阈值 {c['threshold']}）" for c in failed],
        action="先与数据源方核对导出规则并重新导出，再跑一次诊断；在此之前不要据此调整预算",
        impact="数据错误会同时污染 CTR / CPA / 归因三类结论",
        spend_share=1.0,
    )


# ---------------------------------------------------------------------------
# R02 归因覆盖率
# ---------------------------------------------------------------------------


def rule_attribution_gap(ctx: DiagnosisInput) -> Optional[Finding]:
    """归因覆盖率过低：不是效果问题，是回传或归因窗口问题。"""
    rate = ctx.metrics.get("derived", {}).get("attributed_rate")
    if rate is None or rate >= ctx.cfg.attribution_floor:
        return None
    conversions = safe_float(ctx.totals.get("conversions"))
    attributed = safe_float(ctx.totals.get("attributed_conversions"))
    return Finding(
        rule_id="R02",
        level="high" if rate < ctx.cfg.attribution_floor * 0.75 else "medium",
        title="归因覆盖率不足，CPA 被系统性高估",
        subject="归因回传链路",
        evidence=[
            f"归因转化 {num(attributed)} / 全部转化 {num(conversions)} = {pct(rate)}",
            f"门店阈值 {pct(ctx.cfg.attribution_floor)}，当前低于阈值 {pct(ctx.cfg.attribution_floor - rate)}",
            f"当前 CPA {money(ctx.global_cpa)}；若按归因口径重算，真实 CPA 会更低",
        ],
        action="核对转化回传埋点、归因窗口与去重规则，先确认是否有点击后延迟转化未计入",
        impact="归因缺失会让所有渠道的 CPA 横向对比失真，容易误砍真实有效的计划",
        metrics={"attributed_rate": rate, "conversions": conversions, "attributed_conversions": attributed},
        spend_share=1.0,
    )


# ---------------------------------------------------------------------------
# R03 / R04 计划层效率问题
# ---------------------------------------------------------------------------


def rule_high_spend_high_cpa(ctx: DiagnosisInput) -> Optional[Finding]:
    """高消耗 + 显著高于全局的 CPA（有转化样本）。"""
    base = ctx.global_cpa
    table = ctx.campaign_table
    if base is None or base <= 0 or table.empty:
        return None

    hits = []
    for row in table.to_dict(orient="records"):
        conversions = safe_float(row.get("conversions"))
        cost = safe_float(row.get("cost"))
        cpa_value = row.get("cpa")
        share = safe_float(row.get("spend_share"))
        if conversions < ctx.cfg.min_conversions or cpa_value is None:
            continue
        if cpa_value > base * ctx.cfg.cpa_multiplier and share >= ctx.cfg.spend_share_floor:
            hits.append({
                "campaign_id": row.get("campaign_id"),
                "cpa": float(cpa_value),
                "cost": cost,
                "conversions": conversions,
                "share": share,
                "excess": clamp(cpa_value / base - 1.0, 0.0, 10.0),
            })
    if not hits:
        return None

    hits.sort(key=lambda h: -h["share"] * h["excess"])
    affected = sum(h["share"] for h in hits)
    return Finding(
        rule_id="R03",
        level="high" if any(h["cpa"] > base * 2 for h in hits) else "medium",
        title=f"{len(hits)} 个计划消耗占比不低、CPA 显著高于全局",
        subject="、".join(str(h["campaign_id"]) for h in hits[:3]) + ("" if len(hits) <= 3 else " 等"),
        evidence=[
            f"全局 CPA {money(base)}（阈值：高于全局 {ctx.cfg.cpa_multiplier}x 且消耗占比 ≥ {pct(ctx.cfg.spend_share_floor)}）"
        ] + [
            f"计划 {h['campaign_id']}：CPA {money(h['cpa'])}（{ratio(h['cpa'] / base)} 全局），"
            f"消耗 {money(h['cost'])}（占比 {pct(h['share'])}），转化 {num(h['conversions'])}，"
            f"样本量已过门槛({ctx.cfg.min_conversions})"
            for h in hits[:5]
        ],
        action=(
            "对上述计划按 CPA 超出比例削减预算，把省下的量交给 CPA 低于全局中位的计划；"
            "削减后留出 3–5 天观察期，避免单次波动误砍"
        ),
        impact=f"合计影响消耗占比 {pct(affected)}；按当前 CPA 差额估算，效率提升空间可量化复算",
        metrics={"baseline_cpa": base, "affected_spend_share": affected, "hits": hits[:10]},
        spend_share=affected,
    )


def rule_high_spend_zero_conversion(ctx: DiagnosisInput) -> Optional[Finding]:
    """高消耗 + 零转化（点击样本已足够，说明不是样本不足）。"""
    table = ctx.campaign_table
    if table.empty:
        return None
    hits = []
    for row in table.to_dict(orient="records"):
        conversions = safe_float(row.get("conversions"))
        clicks = safe_float(row.get("clicks"))
        share = safe_float(row.get("spend_share"))
        if conversions == 0 and clicks >= ctx.cfg.min_clicks and share >= ctx.cfg.spend_share_floor:
            hits.append({
                "campaign_id": row.get("campaign_id"),
                "cost": safe_float(row.get("cost")),
                "clicks": clicks,
                "share": share,
                "ctr": row.get("ctr"),
            })
    if not hits:
        return None
    hits.sort(key=lambda h: -h["share"])
    affected = sum(h["share"] for h in hits)
    return Finding(
        rule_id="R04",
        level="high",
        title=f"{len(hits)} 个计划有足够点击、零转化，消耗占比 {pct(affected)}",
        subject="、".join(str(h["campaign_id"]) for h in hits[:3]) + ("" if len(hits) <= 3 else " 等"),
        evidence=[
            f"判定门槛：点击 ≥ {ctx.cfg.min_clicks}（排除样本不足）且消耗占比 ≥ {pct(ctx.cfg.spend_share_floor)}"
        ] + [
            f"计划 {h['campaign_id']}：点击 {num(h['clicks'])}、转化 0、消耗 {money(h['cost'])}"
            f"（占比 {pct(h['share'])}）、CTR {pct(h['ctr'])}"
            for h in hits[:5]
        ],
        action=(
            "先查落地页成功率与转化回传状态（零转化更常见于链路故障而非流量问题）；"
            "链路正常则暂停这批准入，把预算转给有转化的计划"
        ),
        impact=f"合计 {pct(affected)} 的消耗当前没有任何转化产出",
        metrics={"affected_spend_share": affected, "hits": hits[:10]},
        spend_share=affected,
    )


# ---------------------------------------------------------------------------
# R05 / R06 结构与长尾
# ---------------------------------------------------------------------------

#: HHI 判定的最小计划数：n 个均分计划的 HHI 恒为 1/n，计划太少时该指标没有信息量
_MIN_CAMPAIGNS_FOR_HHI = 5


def rule_concentration_risk(ctx: DiagnosisInput) -> Optional[Finding]:
    """消耗过度集中在少数计划上（结构性风险）。

    加一条前提：计划数太少时 HHI 天然就高（n 个均分计划的 HHI 恒为 ``1/n``，
    3 个计划就是 0.333），此时报警没有信息量。因此只在计划数足够时才判定。
    """
    hhi_value = ctx.concentration.get("hhi")
    top1 = ctx.concentration.get("top1_share")
    if hhi_value is None:
        return None
    if len(ctx.campaign_table) < _MIN_CAMPAIGNS_FOR_HHI:
        return None
    if hhi_value <= ctx.cfg.hhi_alert and (top1 or 0) <= ctx.cfg.top1_share_alert:
        return None
    return Finding(
        rule_id="R05",
        level="medium",
        title="消耗集中度偏高，单个计划衰退就会带崩整体成本",
        subject="消耗分布结构",
        evidence=[
            f"HHI {hhi_value:.4f}（阈值 {ctx.cfg.hhi_alert}）；前 1 计划占比 {pct(top1)}（阈值 {pct(ctx.cfg.top1_share_alert)}）",
            f"前 3 计划占比 {pct(ctx.concentration.get('top3_share'))}；基尼系数 {num(ctx.concentration.get('gini'), 3)}",
        ],
        action="为头部计划各准备一条备选素材/定向做对冲；把新增预算优先投给第 3–8 名计划，降低单一计划权重",
        impact="集中度不直接损失效率，但会放大波动：一次素材衰退的损失上限由集中度决定",
        metrics={"hhi": hhi_value, "top1_share": top1, "top3_share": ctx.concentration.get("top3_share")},
        spend_share=safe_float(top1),
    )


def rule_long_tail_void_spend(ctx: DiagnosisInput) -> Optional[Finding]:
    """长尾零转化计划的消耗合计占比过高（小钱持续漏）。"""
    table = ctx.campaign_table
    if table.empty:
        return None
    tail_share = 0.0
    tail_rows = []
    for row in table.to_dict(orient="records"):
        conversions = safe_float(row.get("conversions"))
        clicks = safe_float(row.get("clicks"))
        share = safe_float(row.get("spend_share"))
        if conversions == 0 and clicks < ctx.cfg.min_clicks:
            tail_share += share
            tail_rows.append(row)
    if tail_share < ctx.cfg.no_conversion_spend_share or not tail_rows:
        return None
    return Finding(
        rule_id="R06",
        level="medium",
        title=f"{len(tail_rows)} 个长尾计划零转化，合计消耗占比 {pct(tail_share)}",
        subject="长尾计划",
        evidence=[
            f"这些计划单个消耗小、点击样本也没到 {ctx.cfg.min_clicks}，因此不参与效率排名",
            f"合计消耗占比 {pct(tail_share)}（阈值 {pct(ctx.cfg.no_conversion_spend_share)}）",
        ],
        action="设置长尾计划的预算上限与淘汰期（例如 7 天零转化即关停），把省下的预算并入头部计划",
        impact="单笔金额小但持续时间长，属于典型的持续性漏损",
        metrics={"tail_count": len(tail_rows), "tail_spend_share": tail_share},
        spend_share=tail_share,
    )


# ---------------------------------------------------------------------------
# R07 / R08 流量成本异常
# ---------------------------------------------------------------------------


def rule_cpm_outlier(ctx: DiagnosisInput) -> Optional[Finding]:
    """CPM 显著偏离全局（高价或低价流量）。"""
    base = ctx.global_cpm
    table = ctx.campaign_table
    if base is None or base <= 0 or table.empty:
        return None

    expensive, cheap = [], []
    for row in table.to_dict(orient="records"):
        impressions = safe_float(row.get("impressions"))
        cpm_value = row.get("cpm")
        if impressions < ctx.cfg.min_impressions or cpm_value is None:
            continue
        if cpm_value > base * ctx.cfg.cpm_high:
            expensive.append((row, float(cpm_value)))
        elif cpm_value < base * ctx.cfg.cpm_low:
            cheap.append((row, float(cpm_value)))

    if not expensive and not cheap:
        return None

    affected = sum(safe_float(r.get("spend_share")) for r, _ in expensive + cheap)
    level = "medium" if expensive else "low"
    title_parts = []
    if expensive:
        title_parts.append(f"{len(expensive)} 个计划 CPM 高于全局 {ctx.cfg.cpm_high}x")
    if cheap:
        title_parts.append(f"{len(cheap)} 个计划 CPM 低于全局 {ctx.cfg.cpm_low}x")

    evidence = [f"全局 CPM {money(base)}；判定门槛：曝光 ≥ {num(ctx.cfg.min_impressions)}"]
    for row, value in expensive[:3]:
        evidence.append(
            f"高价：计划 {row.get('campaign_id')} CPM {money(value)}（{ratio(value / base)} 全局），"
            f"占曝光 {num(safe_float(row.get('impressions')))}、消耗占比 {pct(safe_float(row.get('spend_share')))}"
        )
    for row, value in cheap[:3]:
        evidence.append(
            f"低价：计划 {row.get('campaign_id')} CPM {money(value)}（{ratio(value / base)} 全局），需确认是否激励流量或口径异常"
        )

    return Finding(
        rule_id="R07",
        level=level,
        title="；".join(title_parts),
        subject="流量成本（CPM）",
        evidence=evidence,
        action=(
            "高价计划先查竞价方式与定向宽度（过窄定向会抬高 CPM），再决定是否降出价；"
            "低价计划核对流量来源与广告位白名单，排除低质流量"
        ),
        impact=f"涉及消耗占比 {pct(affected)}；CPM 是 CPC 与 CPA 的成本源头，先修这一层见效最快",
        metrics={"baseline_cpm": base, "expensive": len(expensive), "cheap": len(cheap)},
        spend_share=affected,
    )


# ---------------------------------------------------------------------------
# R08 / R09 比率显著低于全局（带显著性检验）
# ---------------------------------------------------------------------------


def rule_low_ctr(ctx: DiagnosisInput) -> Optional[Finding]:
    """CTR 显著低于全局（素材/定向问题，而非成本问题）。"""
    base = ctx.global_ctr
    table = ctx.campaign_table
    if base is None or base <= 0 or table.empty:
        return None

    total_impressions = safe_float(ctx.totals.get("impressions"))
    total_clicks = safe_float(ctx.totals.get("clicks"))

    hits = []
    for row in table.to_dict(orient="records"):
        impressions = safe_float(row.get("impressions"))
        clicks = safe_float(row.get("clicks"))
        ctr_value = row.get("ctr")
        if impressions < ctx.cfg.min_impressions or ctr_value is None:
            continue
        if ctr_value >= base * ctx.cfg.ctr_low:
            continue
        # 与「全局口径去掉自身」对比，避免自身把基准拉低
        rest_n = total_impressions - impressions
        rest_x = total_clicks - clicks
        test = two_proportion_z_test(rest_x, rest_n, clicks, impressions, alpha=ctx.cfg.z_alpha)
        if not test.get("significant"):
            continue
        hits.append({
            "campaign_id": row.get("campaign_id"),
            "ctr": float(ctr_value),
            "impressions": impressions,
            "clicks": clicks,
            "share": safe_float(row.get("spend_share")),
            "p_value": test.get("p_value"),
        })

    if not hits:
        return None
    hits.sort(key=lambda h: h["p_value"] or 1.0)
    affected = sum(h["share"] for h in hits)
    return Finding(
        rule_id="R08",
        level="medium",
        title=f"{len(hits)} 个计划 CTR 显著低于全局（已过显著性检验）",
        subject="、".join(str(h["campaign_id"]) for h in hits[:3]) + ("" if len(hits) <= 3 else " 等"),
        evidence=[
            f"全局 CTR {pct(base)}；门槛：低于全局 {ctx.cfg.ctr_low}x 且双侧 z 检验 p < {ctx.cfg.z_alpha}",
        ] + [
            f"计划 {h['campaign_id']}：CTR {pct(h['ctr'])}（{ratio(h['ctr'] / base)} 全局），"
            f"曝光 {num(h['impressions'])}，p = {num(h['p_value'], 6)}"
            for h in hits[:5]
        ],
        action="优先换素材前 3 秒与首图（CTR 是素材问题）；若素材已多次迭代仍低，则收窄定向重新测试",
        impact=f"涉及消耗占比 {pct(affected)}；CTR 低会同时抬高 CPC 与 CPA，属于上游问题",
        metrics={"baseline_ctr": base, "hits": hits[:10]},
        spend_share=affected,
    )


def rule_low_cvr(ctx: DiagnosisInput) -> Optional[Finding]:
    """CVR 显著低于全局（落地页/转化链路问题，区别于 CTR 问题）。"""
    base = ctx.global_cvr
    table = ctx.campaign_table
    if base is None or base <= 0 or table.empty:
        return None

    total_clicks = safe_float(ctx.totals.get("clicks"))
    total_conversions = safe_float(ctx.totals.get("conversions"))

    hits = []
    for row in table.to_dict(orient="records"):
        clicks = safe_float(row.get("clicks"))
        conversions = safe_float(row.get("conversions"))
        cvr_value = row.get("cvr")
        if clicks < ctx.cfg.min_clicks or cvr_value is None:
            continue
        if cvr_value >= base * 0.5:
            continue
        rest_n = total_clicks - clicks
        rest_x = total_conversions - conversions
        test = two_proportion_z_test(rest_x, rest_n, conversions, clicks, alpha=ctx.cfg.z_alpha)
        if not test.get("significant"):
            continue
        hits.append({
            "campaign_id": row.get("campaign_id"),
            "cvr": float(cvr_value),
            "clicks": clicks,
            "conversions": conversions,
            "share": safe_float(row.get("spend_share")),
            "p_value": test.get("p_value"),
        })

    if not hits:
        return None
    hits.sort(key=lambda h: h["p_value"] or 1.0)
    affected = sum(h["share"] for h in hits)
    return Finding(
        rule_id="R09",
        level="medium",
        title=f"{len(hits)} 个计划转化率显著低于全局",
        subject="、".join(str(h["campaign_id"]) for h in hits[:3]) + ("" if len(hits) <= 3 else " 等"),
        evidence=[
            f"全局 CVR {pct(base)}；门槛：点击 ≥ {ctx.cfg.min_clicks} 且双侧 z 检验 p < {ctx.cfg.z_alpha}",
        ] + [
            f"计划 {h['campaign_id']}：CVR {pct(h['cvr'])}（{ratio(h['cvr'] / base)} 全局），"
            f"点击 {num(h['clicks'])} → 转化 {num(h['conversions'])}，p = {num(h['p_value'], 6)}"
            for h in hits[:5]
        ],
        action="查落地页首屏与表单步骤（CVR 问题多在承接页），并核对转化回传是否覆盖全部转化类型",
        impact=f"涉及消耗占比 {pct(affected)}；同样点击量下 CVR 低意味着 CPA 直接抬高",
        metrics={"baseline_cvr": base, "hits": hits[:10]},
        spend_share=affected,
    )


# ---------------------------------------------------------------------------
# R10 时段机会（多重比较已校正）
# ---------------------------------------------------------------------------


def rule_hourly_opportunity(ctx: DiagnosisInput) -> Optional[Finding]:
    """某些时段 CTR 显著高于全局——这是可以直接拿走的增量。"""
    hourly = ctx.hourly
    base = ctx.global_ctr
    if hourly.empty or base is None or base <= 0:
        return None

    total_impressions = safe_float(ctx.totals.get("impressions"))
    total_clicks = safe_float(ctx.totals.get("clicks"))
    hours = len(hourly) or 1
    corrected_alpha = ctx.cfg.z_alpha / hours  # Bonferroni：按时段数校正

    hits = []
    for row in hourly.to_dict(orient="records"):
        impressions = safe_float(row.get("impressions"))
        clicks = safe_float(row.get("clicks"))
        if impressions < ctx.cfg.min_impressions:
            continue
        rest_n = total_impressions - impressions
        rest_x = total_clicks - clicks
        if rest_n <= 0:
            continue
        test = two_proportion_z_test(rest_x, rest_n, clicks, impressions, alpha=corrected_alpha)
        if not test.get("significant"):
            continue
        hits.append({
            "hour": int(safe_float(row.get("hour"))),
            "ctr": row.get("ctr"),
            "impressions": impressions,
            "clicks": clicks,
            "cpa": row.get("cpa"),
            "p_value": test.get("p_value"),
        })

    if not hits:
        return None
    hits.sort(key=lambda h: -(h["ctr"] or 0.0))
    best = hits[0]
    return Finding(
        rule_id="R10",
        level="low",
        title=f"{len(hits)} 个时段 CTR 显著高于全局，存在可分时加价的机会",
        subject="、".join(f"{h['hour']:02d}:00" for h in hits[:6]),
        evidence=[
            f"全局 CTR {pct(base)}；已按 {hours} 个时段做 Bonferroni 校正（名义 α = {num(corrected_alpha, 6)}），避免多重比较误报",
            f"最优时段 {best['hour']:02d}:00：CTR {pct(best['ctr'])}（{ratio((best['ctr'] or 0) / base)} 全局），"
            f"曝光 {num(best['impressions'])}，p = {num(best['p_value'], 6)}"
            if best["ctr"] else "最优时段数据不完整",
            f"该时段 CPA {money(best['cpa'])}（全局 {money(ctx.global_cpa)}）",
        ],
        action="对高 CTR 时段做分时出价上浮（先 10%–20%），并单独跑一次时段 A/B 实验确认增量",
        impact="时段机会通常无需新增预算，靠重分配即可拿到增量；建议先小比例验证",
        metrics={"hours": [h["hour"] for h in hits], "corrected_alpha": corrected_alpha},
        spend_share=0.0,
    )


# ---------------------------------------------------------------------------
# R11 / R12 趋势与节奏
# ---------------------------------------------------------------------------


def rule_cpa_trend_worsening(ctx: DiagnosisInput) -> Optional[Finding]:
    """最近几天的 CPA 相对此前明显走高（效果衰退或竞争加剧）。"""
    daily = ctx.daily
    cfg = ctx.cfg
    needed = cfg.trend_lookback_days + cfg.trend_recent_days
    if daily.empty or len(daily) < needed:
        return None

    ordered = daily.sort_values("date").reset_index(drop=True)
    recent = ordered.tail(cfg.trend_recent_days)
    previous = ordered.iloc[-(cfg.trend_recent_days + cfg.trend_lookback_days):-cfg.trend_recent_days]

    recent_conv = safe_float(recent["conversions"].sum())
    previous_conv = safe_float(previous["conversions"].sum())
    if recent_conv < cfg.min_conversions or previous_conv < cfg.min_conversions:
        return None  # 样本不足不下结论

    recent_cpa = safe_div(safe_float(recent["cost"].sum()), recent_conv)
    previous_cpa = safe_div(safe_float(previous["cost"].sum()), previous_conv)
    change = relative_change(previous_cpa, recent_cpa)
    if change is None or change <= cfg.trend_cpa_rise:
        return None

    return Finding(
        rule_id="R11",
        level="medium" if change <= cfg.trend_cpa_rise * 2 else "high",
        title=f"近 {cfg.trend_recent_days} 天 CPA 环比走高 {signed_pct(change)}",
        subject=f"最近 {cfg.trend_recent_days} 天 vs 前 {cfg.trend_lookback_days} 天",
        evidence=[
            f"近期 CPA {money(recent_cpa)}（转化 {num(recent_conv)}）",
            f"此前 CPA {money(previous_cpa)}（转化 {num(previous_conv)}）",
            f"变化 {signed_pct(change)}，告警阈值 {signed_pct(cfg.trend_cpa_rise)}；两段转化数均 ≥ {cfg.min_conversions}，样本量达标",
        ],
        action="按「素材衰退 → 竞争加剧 → 流量质量变化」顺序排查；先换素材并观察 3 天，未见回落再考虑收窄定向或降出价",
        impact="趋势恶化是复利型问题：不处理会逐日抬高整体 CPA",
        metrics={"recent_cpa": recent_cpa, "previous_cpa": previous_cpa, "change": change},
        spend_share=1.0,
    )


def rule_daily_cost_anomaly(ctx: DiagnosisInput) -> Optional[Finding]:
    """单日消耗显著偏离自身分布（投放节奏异常或漏跑）。"""
    daily = ctx.daily
    if daily.empty or len(daily) < 5:
        return None
    costs = [safe_float(v) for v in daily["cost"].tolist()]
    latest_row = daily.sort_values("date").iloc[-1]
    latest = safe_float(latest_row["cost"])
    score = zscore(latest, costs)
    if score is None or abs(score) < 2.0:
        return None
    direction = "高于" if score > 0 else "低于"
    return Finding(
        rule_id="R12",
        level="low",
        title=f"最新一日消耗异常{direction}历史分布（z = {score:.2f}）",
        subject=str(latest_row["date"]),
        evidence=[
            f"该日消耗 {money(latest)}，历史均值 {money(sum(costs) / len(costs))}",
            f"标准分 z = {score:.2f}（门槛 |z| ≥ 2），样本 {len(costs)} 天",
        ],
        action="确认是投放策略调整、预算改动，还是数据未跑完/漏跑；非人为原因时按常规排障流程处理",
        impact="节奏异常会让趋势结论失真，需先判定是否为口径问题",
        metrics={"z": score, "latest_cost": latest, "days": len(costs)},
        spend_share=0.0,
    )


#: 规则注册表（顺序即报告中的默认展示顺序，最终按优先级重排）
RULES: Sequence[Any] = (
    rule_quality_block,
    rule_attribution_gap,
    rule_high_spend_high_cpa,
    rule_high_spend_zero_conversion,
    rule_concentration_risk,
    rule_long_tail_void_spend,
    rule_cpm_outlier,
    rule_low_ctr,
    rule_low_cvr,
    rule_hourly_opportunity,
    rule_cpa_trend_worsening,
    rule_daily_cost_anomaly,
)


def run_rules(ctx: DiagnosisInput, *, rules: Sequence[Any] = RULES) -> List[Finding]:
    """依次执行规则；单条规则失败不影响其他规则（返回值里记一条降级说明）。

    Returns:
        未排序的发现列表（排序交给 :func:`adops.diagnose.scoring.rank_findings`）。
    """
    findings: List[Finding] = []
    for rule in rules:
        try:
            finding = rule(ctx)
        except Exception as exc:  # pragma: no cover - 防御性：单条规则坏掉不该拖垮整份报告
            findings.append(Finding(
                rule_id=getattr(rule, "__name__", "unknown"),
                level="low",
                title="规则执行异常，该条诊断本次跳过",
                subject="内部错误",
                evidence=[f"{type(exc).__name__}: {exc}"],
                action="检查输入数据列与样本量是否满足该规则前提",
            ))
            continue
        if finding is not None:
            findings.append(finding)
    return findings
