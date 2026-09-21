# -*- coding: utf-8 -*-
"""通用小工具：格式化与数值处理。

单独成模块的原因：诊断规则与报告层都要把同一批数字渲染成中文句子，
如果各写一份格式化函数，报表里同一个指标会出现两种写法（0.008 vs 0.80%），
这在评审时会被直接质疑口径。
"""
from __future__ import annotations

from typing import Any, Optional

Number = Optional[float]


def clamp(value: float, low: float, high: float) -> float:
    """把数值限制在 ``[low, high]`` 区间内。"""
    return max(low, min(high, value))


def is_blank(value: Any) -> bool:
    """判断值是否为空（None / NaN / 非数值字符串）。"""
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    try:
        import pandas as pd  # 局部导入，避免纯数值场景也拖依赖

        return bool(pd.isna(value))
    except Exception:  # pragma: no cover - 无 pandas 环境下退化为 None 判断
        return False


def pct(value: Any, digits: int = 2) -> str:
    """小数 → 百分比字符串；空值渲染为 ``—``。"""
    if is_blank(value):
        return "—"
    return f"{float(value) * 100:.{digits}f}%"


def money(value: Any, digits: int = 4) -> str:
    """金额（元）格式化。"""
    if is_blank(value):
        return "—"
    return f"{float(value):,.{digits}f} 元"


def num(value: Any, digits: int = 0) -> str:
    """数字千分位格式化。"""
    if is_blank(value):
        return "—"
    if digits == 0:
        return f"{float(value):,.0f}"
    return f"{float(value):,.{digits}f}"


def ratio(value: Any, digits: int = 2) -> str:
    """倍数/比值格式化。"""
    if is_blank(value):
        return "—"
    return f"{float(value):.{digits}f}x"


def signed_pct(value: Any, digits: int = 1) -> str:
    """带正负号的百分比（用于变化率）。"""
    if is_blank(value):
        return "—"
    return f"{float(value) * 100:+.{digits}f}%"


def safe_float(value: Any, default: float = 0.0) -> float:
    """尽力转 float，失败返回默认值。"""
    if is_blank(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
