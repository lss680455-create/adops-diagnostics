# -*- coding: utf-8 -*-
"""adops-diagnostics：广告投放数据诊断流水线。

把「投放日志 → 清洗 → 质量体检 → 指标 → 诊断 → 实验建议 → 报告」七个阶段
显式化成可单测的流水线；诊断结论由确定性规则产出，AI 只负责把它写成能读的
话，并且每个数字都必须回指到证据表（见 :mod:`adops.ai.guardrail`）。
"""
from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
