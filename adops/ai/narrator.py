# -*- coding: utf-8 -*-
"""AI 叙述层：把确定性诊断结果写成能读的话。

分工是刻意的：

- **结论由规则产出**（:mod:`adops.diagnose`），数字来自证据表，可复算；
- **AI 只负责表达**：把「R03 高消耗高 CPA」写成运营看得懂的一句话，并且
  必须过 :mod:`adops.ai.guardrail` 的数字回指校验；过不了就回落到模板；
- **零密钥也能跑**：默认走模板，结论与启用 AI 时完全一致，只是行文朴素一些。

这条分工就是「AI 能力」在投放业务里的正确落点：AI 提升的是可读性与交付速度，
不是替你决定砍哪个计划。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..config import LLMConfig
from ..util import clamp, money, num, pct, ratio, signed_pct
from .guardrail import guard_or_fallback

#: 叙述里最多展开的问题条数
MAX_FINDINGS_IN_NARRATIVE = 5


def build_narrative_payload(
    *,
    account: str,
    metrics: Dict[str, Any],
    quality: Dict[str, Any],
    concentration: Dict[str, Any],
    findings: Sequence[Any],
    summary: Dict[str, Any],
    spend_shift: Optional[Dict[str, Any]] = None,
    cost_unit: str = "元",
) -> Dict[str, Any]:
    """组装叙述层输入。

    这个 payload 同时是**证据表**：守卫校验数字时用的就是它。因此这里只放
    确定性的、已经算好的数字，不放任何解释性文字——否则模型可以「引用」
    一段不存在证据的叙述。
    """
    top = list(findings)[:MAX_FINDINGS_IN_NARRATIVE]
    return {
        "account": account,
        "cost_unit": cost_unit,
        "kpi": {
            "impressions": metrics.get("totals", {}).get("impressions"),
            "clicks": metrics.get("totals", {}).get("clicks"),
            "conversions": metrics.get("totals", {}).get("conversions"),
            "cost": metrics.get("totals", {}).get("cost"),
            "ctr": metrics.get("derived", {}).get("ctr"),
            "cvr": metrics.get("derived", {}).get("cvr"),
            "cpc": metrics.get("derived", {}).get("cpc"),
            "cpm": metrics.get("derived", {}).get("cpm"),
            "cpa": metrics.get("derived", {}).get("cpa"),
            "attributed_rate": metrics.get("derived", {}).get("attributed_rate"),
        },
        "quality": {
            "verdict": quality.get("verdict"),
            "failed": quality.get("failed"),
            "warned": quality.get("warned"),
            "rows": quality.get("rows"),
            "distinct_days": quality.get("distinct_days"),
        },
        "concentration": dict(concentration or {}),
        "summary": dict(summary or {}),
        "findings": [f.to_dict() if hasattr(f, "to_dict") else dict(f) for f in top],
        "spend_shift": spend_shift or {},
    }


def _finding_line(finding: Dict[str, Any]) -> str:
    level = {"high": "高", "medium": "中", "low": "低"}.get(finding.get("level"), "低")
    return (
        f"- [{level}] {finding.get('title')}（{finding.get('subject')}）："
        f"{finding.get('action')}"
    )


def _money(value: Any, unit: str = "元", digits: int = 4) -> str:
    """按数据口径渲染金额。

    公开数据集的消耗不是真实货币，报告里必须写成「口径单位」而不是「元」——
    同一份报告里出现「0.0060 元」的 CPA 会被直接质疑，而它其实只是口径单位。
    """
    if value is None:
        return "—"
    try:
        rendered = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"
    return rendered if unit in ("", None) else f"{rendered} {unit}"


def template_narrative(payload: Dict[str, Any]) -> str:
    """确定性模板叙述：零密钥、可复算，作为 AI 的回落与对照基线。"""
    kpi = payload.get("kpi", {})
    quality = payload.get("quality", {})
    summary = payload.get("summary", {})
    findings = payload.get("findings", []) or []
    unit = payload.get("cost_unit", "元")

    lines: List[str] = []
    verdict = quality.get("verdict", "")
    if summary.get("counts", {}).get("high"):
        lines.append(
            f"本次共发现 {summary.get('total', 0)} 项问题，其中高优先级 "
            f"{summary['counts']['high']} 项，问题影响的消耗占比合计 "
            f"{pct(summary.get('affected_spend_share'))}"
            + ("（原始合计 " + pct(summary.get("affected_spend_share_raw")) + "，各项有重叠，不可相加）"
               if summary.get("overlapping") else "")
            + f"；数据体检结论：{verdict}。"
        )
    else:
        lines.append(
            f"本次共发现 {summary.get('total', 0)} 项问题，无高优先级项；"
            f"数据体检结论：{verdict}。"
        )

    lines.append(
        f"整体表现：曝光 {num(kpi.get('impressions'))}、点击 {num(kpi.get('clicks'))}、"
        f"转化 {num(kpi.get('conversions'))}、消耗 {_money(kpi.get('cost'), unit, 2)}；"
        f"CTR {pct(kpi.get('ctr'))}、CVR {pct(kpi.get('cvr'))}、"
        f"CPC {_money(kpi.get('cpc'), unit)}、CPM {_money(kpi.get('cpm'), unit)}、"
        f"CPA {_money(kpi.get('cpa'), unit)}。"
    )

    if findings:
        lines.append("按优先级排序的处理清单：")
        lines.extend(_finding_line(f) for f in findings)
    else:
        lines.append("未触发任何诊断规则，当前大盘结构与效率均在阈值内。")

    shift = payload.get("spend_shift") or {}
    if shift.get("moves"):
        lines.append(
            f"预算再分配建议（确定性算法，可复算）：挪动 {_money(shift.get('moved_cost'), unit, 2)}，"
            f"涉及 {len(shift['moves'])} 个计划；预计 CPA 从 {_money(shift.get('cpa_before'), unit)} "
            f"降至 {_money(shift.get('cpa_after'), unit)}。"
        )
    return "\n\n".join(lines)


def narrate(
    payload: Dict[str, Any],
    llm_cfg: Optional[LLMConfig] = None,
    *,
    llm_client: Any = None,
    extra_allowed: Sequence[float] = (),
) -> Dict[str, Any]:
    """产出叙述结果。

    Args:
        payload: :func:`build_narrative_payload` 的输出（同时充当证据表）。
        llm_cfg: LLM 配置；未启用或不可用时直接走模板。
        llm_client: 可选注入的客户端（便于测试时打桩，避免真实网络调用）。
        extra_allowed: 额外允许的数字（如配置阈值），用于守卫校验。

    Returns:
        ``{text, mode, guard}``；``mode`` 为 ``template`` 或 ``llm``。
    """
    fallback = template_narrative(payload)
    enabled = bool(llm_cfg and llm_cfg.usable)
    client = llm_client
    if enabled and client is None:
        try:
            from ..llm.client import LLMClient

            client = LLMClient(llm_cfg)
        except Exception:  # pragma: no cover - 客户端不可用则直接回落
            enabled = False
            client = None

    if not enabled or client is None:
        return {
            "text": fallback,
            "mode": "template",
            "guard": {"ok": True, "checked": 0, "unbound": [], "note": "未启用 LLM，使用确定性模板"},
        }

    from .prompts import NARRATIVE_SYSTEM_PROMPT

    try:
        candidate = client.complete(NARRATIVE_SYSTEM_PROMPT, _render_evidence(payload), purpose="narrative")
    except Exception as exc:  # 网络/配额失败一律降级，不中断流水线
        return {
            "text": fallback,
            "mode": "template",
            "guard": {
                "ok": True, "checked": 0, "unbound": [],
                "fallback_reason": f"LLM 调用失败（{type(exc).__name__}），已回落到确定性模板",
            },
        }

    outcome = guard_or_fallback(
        candidate or "", payload, fallback,
        extra_allowed=list(extra_allowed) + list(_config_numbers(payload)),
    )
    outcome["model"] = getattr(llm_cfg, "model", "")
    return outcome


def _config_numbers(payload: Dict[str, Any]) -> List[float]:
    """从 payload 里顺便允许「门槛类」整数（如样本量门槛），减少误报回落。"""
    extra: List[float] = []
    for finding in payload.get("findings", []) or []:
        for value in (finding.get("metrics") or {}).values():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                extra.append(float(value))
        extra.append(float(finding.get("spend_share") or 0.0))
    return extra


def _render_evidence(payload: Dict[str, Any]) -> str:
    """把证据表渲染成给模型的输入文本（含全部数字，便于回指校验）。"""
    lines = ["以下是已经算好的确定性诊断结果，请只用其中的数字组织语言：", ""]
    kpi = payload.get("kpi", {})
    lines.append("【核心指标】")
    for key, value in kpi.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("【数据体检】")
    for key, value in (payload.get("quality") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("【消耗结构】")
    for key, value in (payload.get("concentration") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("【诊断发现（已按优先级排序）】")
    for finding in payload.get("findings", []) or []:
        lines.append(
            f"- {finding.get('rule_id')} [{finding.get('level')}] {finding.get('title')} | "
            f"对象={finding.get('subject')} | 影响占比={finding.get('spend_share')} | "
            f"建议={finding.get('action')} | 证据={'；'.join(finding.get('evidence', []))}"
        )
    shift = payload.get("spend_shift") or {}
    if shift:
        lines.append("")
        lines.append("【预算再分配建议】")
        for key, value in shift.items():
            if key != "moves":
                lines.append(f"- {key}: {value}")
    return "\n".join(lines)
