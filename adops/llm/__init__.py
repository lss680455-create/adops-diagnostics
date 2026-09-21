# -*- coding: utf-8 -*-
"""LLM 层公开入口。"""
from __future__ import annotations

from .client import LLMClient, LLMError, available  # noqa: F401

__all__ = ["LLMClient", "LLMError", "available"]
