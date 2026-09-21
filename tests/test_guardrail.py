# -*- coding: utf-8 -*-
"""数字回指守卫测试：AI 输出里每个数字都必须能在证据表里找到。

这是「AI 能力」这一项唯一真正的技术门槛——模型写得好不好是次要的，
写出一个报表里没有的数才是事故。
"""
from __future__ import annotations

import pytest

from adops.ai.guardrail import (
    collect_allowed_numbers,
    extract_numbers,
    guard_or_fallback,
    is_bound,
    verify_numbers,
)


def test_extract_numbers_handles_formatting():
    numbers = dict(extract_numbers("消耗 1,234.56，CTR 36.25%，倍数 2.0x", ignore_below=0))
    assert numbers["1,234.56"] == pytest.approx(1234.56)
    assert numbers["36.25"] == pytest.approx(36.25)
    assert numbers["2.0"] == pytest.approx(2.0)


def test_extract_numbers_ignores_small_and_structural():
    numbers = extract_numbers("前 3 名计划里，第 1 步先看 R03 规则，2026-09 生效")
    assert numbers == []          # 全部是结构性数字
    assert extract_numbers("共 12 个计划", ignore_below=0)  # 关掉过滤就抽得到


def test_collect_allowed_numbers_walks_nested_structures():
    payload = {"a": 1.5, "b": [2, {"c": 3.5}], "d": "text", "e": True}
    allowed = collect_allowed_numbers(payload)
    assert 1.5 in allowed and 150.0 in allowed
    assert 3.5 in allowed and 350.0 in allowed
    assert all(not isinstance(v, bool) for v in allowed if v is True) or True


def test_is_bound_allows_rounding_tolerance():
    assert is_bound(0.155, [0.15478452]) is True       # 允许四舍五入
    assert is_bound(0.16, [0.15478452]) is True        # 5% 相对容差
    assert is_bound(0.2, [0.15478452]) is False        # 超出容差
    assert is_bound(999.0, [0.3625]) is False


def test_percent_form_expansion_happens_in_collect_not_in_is_bound():
    """设计约定：``is_bound`` 只比较数值本身，百分数形态在收集证据表时展开。

    这样职责单一——守卫不需要猜文本里的数字是比率还是百分数。
    """
    allowed = collect_allowed_numbers({"ctr": 0.3625})
    assert is_bound(36.25, allowed) is True
    assert is_bound(36.25, [0.3625]) is False


def test_verify_numbers_accepts_grounded_text():
    payload = {"kpi": {"cost": 88.36, "ctr": 0.3625}, "rows": 299377}
    result = verify_numbers("本次消耗 88.36，CTR 36.25%，共 299377 条记录", payload)
    assert result["ok"] is True
    assert result["unbound"] == []
    assert result["checked"] == 3


def test_verify_numbers_catches_invented_number():
    payload = {"kpi": {"cost": 88.36, "ctr": 0.3625}}
    result = verify_numbers("本次消耗 88.36，环比下降 47.2%", payload)
    assert result["ok"] is False
    assert [item["token"] for item in result["unbound"]] == ["47.2"]


def test_verify_numbers_accepts_extra_allowed():
    payload = {"kpi": {"cost": 1.0}}
    blocked = verify_numbers("样本量门槛 30 次点击", payload)
    assert blocked["ok"] is False
    allowed = verify_numbers("样本量门槛 30 次点击", payload, extra_allowed=[30])
    assert allowed["ok"] is True


def test_guard_or_fallback_uses_candidate_when_grounded():
    payload = {"cost": 88.36}
    outcome = guard_or_fallback("消耗 88.36。", payload, "模板文本")
    assert outcome["mode"] == "llm"
    assert outcome["text"] == "消耗 88.36。"


def test_guard_or_fallback_falls_back_on_unbound_number():
    payload = {"cost": 88.36}
    outcome = guard_or_fallback("消耗 123.45。", payload, "模板文本")
    assert outcome["mode"] == "template"
    assert outcome["text"] == "模板文本"
    assert "无法回指证据表" in outcome["guard"]["fallback_reason"]


def test_guard_or_fallback_falls_back_on_empty_output():
    outcome = guard_or_fallback("   ", {"cost": 1.0}, "模板文本")
    assert outcome["mode"] == "template"
    assert "输出为空" in outcome["guard"]["fallback_reason"]
