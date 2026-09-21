# -*- coding: utf-8 -*-
"""叙述层测试：模板兜底、AI 守卫、失败降级。

不联网：LLM 客户端用打桩对象注入。
"""
from __future__ import annotations

import pytest

from adops.ai.narrator import build_narrative_payload, narrate, template_narrative
from adops.config import LLMConfig
from adops.metrics.ads import build_metrics

from conftest import make_diagnosis_input, make_frame


class StubClient:
    """打桩 LLM 客户端：返回预设文本，或抛出预设异常。"""

    def __init__(self, text: str = "", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str, *, purpose: str = "x") -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert "数字" in system_prompt       # 提示词必须带数字约束
        assert "确定性诊断结果" in user_prompt  # 证据表必须渲染进提示词
        return self.text


def _payload(cost_unit: str = "元"):
    frame = make_frame([
        {"campaign_id": "A", "impressions": 10_000, "clicks": 100, "conversions": 10,
         "cost": 50.0},
    ])
    ctx = make_diagnosis_input(frame)
    from adops.diagnose.scoring import rank_findings, summarize

    findings = rank_findings([])
    return build_narrative_payload(
        account="测试账户",
        metrics=ctx.metrics,
        quality=ctx.quality,
        concentration=ctx.concentration,
        findings=findings,
        summary=summarize(findings),
        spend_shift=None,
        cost_unit=cost_unit,
    )


def test_template_narrative_contains_kpi_numbers():
    text = template_narrative(_payload())
    assert "10,000" in text          # 曝光
    assert "1.00%" in text           # CTR
    assert "50.00" in text           # 消耗


def test_template_narrative_respects_cost_unit():
    text = template_narrative(_payload(cost_unit="口径单位"))
    assert "口径单位" in text
    assert "元" not in text.replace("口径单位", "")


def test_narrate_without_llm_uses_template():
    outcome = narrate(_payload(), LLMConfig(enabled=False))
    assert outcome["mode"] == "template"
    assert "未启用 LLM" in outcome["guard"]["note"]


def test_narrate_uses_llm_when_numbers_are_grounded():
    payload = _payload()
    grounded = "本次整体 CTR 为 1.00%，消耗 50.00。"
    outcome = narrate(payload, LLMConfig(enabled=True, api_key="k"), llm_client=StubClient(grounded))
    assert outcome["mode"] == "llm"
    assert outcome["text"] == grounded
    assert outcome["guard"]["ok"] is True


def test_narrate_falls_back_when_llm_invents_numbers():
    payload = _payload()
    outcome = narrate(payload, LLMConfig(enabled=True, api_key="k"),
                      llm_client=StubClient("效果提升了 47.20%，建议加预算。"))
    assert outcome["mode"] == "template"
    assert "无法回指证据表" in outcome["guard"]["fallback_reason"]
    assert "1.00%" in outcome["text"]   # 回落到模板，模板里的数字仍在


def test_narrate_falls_back_on_llm_failure():
    payload = _payload()
    outcome = narrate(payload, LLMConfig(enabled=True, api_key="k"),
                      llm_client=StubClient(error=RuntimeError("网络断了")))
    assert outcome["mode"] == "template"
    assert "LLM 调用失败" in outcome["guard"]["fallback_reason"]


def test_narrate_skips_llm_without_api_key():
    client = StubClient("消耗 50.00")
    outcome = narrate(_payload(), LLMConfig(enabled=True, api_key=""), llm_client=client)
    assert outcome["mode"] == "template"
    assert client.calls == 0


def test_payload_is_also_the_evidence_table():
    """payload 里只能放确定性数字 —— 守卫就是拿它当证据表用的。"""
    payload = _payload()
    assert set(payload["kpi"]) >= {"impressions", "clicks", "conversions", "cost", "ctr", "cpa"}
    assert isinstance(payload["summary"], dict)
    assert isinstance(payload["findings"], list)
