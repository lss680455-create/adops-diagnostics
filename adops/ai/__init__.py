# -*- coding: utf-8 -*-
"""AI 层公开入口：叙述生成 + 数字回指守卫。"""
from __future__ import annotations

from .guardrail import (  # noqa: F401
    collect_allowed_numbers,
    extract_numbers,
    guard_or_fallback,
    is_bound,
    verify_numbers,
)
from .narrator import build_narrative_payload, narrate, template_narrative  # noqa: F401

__all__ = [
    "build_narrative_payload",
    "collect_allowed_numbers",
    "extract_numbers",
    "guard_or_fallback",
    "is_bound",
    "narrate",
    "template_narrative",
    "verify_numbers",
]
