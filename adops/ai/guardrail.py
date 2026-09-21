# -*- coding: utf-8 -*-
"""数字回指守卫：AI 写出来的每个数字都必须能在证据表里找到。

为什么需要这一层：让模型「总结一下这份投放报表」最危险的失败不是写得不好，
而是**写出一个报表里没有的数**——运营照着这个数去砍预算，事后没人能复现。
这类错误在人工审核下也常被漏掉，因为数字看起来都很合理。

所以把纪律做成代码：模型输出里的每一个数字，要么能在证据表里按容差匹配到，
要么整段输出作废、回落到确定性模板。容差是为了允许四舍五入（模型常把
0.15478 写成 0.155）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: 数字 token：可带千分位、正负号、小数点；后面可选 % 或 x
_NUMBER_PATTERN = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")

#: 单/双位整数默认不校验：文本里的「前 3 名」「第一步」属于结构性数字
_DEFAULT_IGNORE_BELOW = 10.0


def _parse(token: str) -> Optional[float]:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def extract_numbers(text: str, *, ignore_below: float = _DEFAULT_IGNORE_BELOW) -> List[Tuple[str, float]]:
    """从文本里抽出所有数字 token。

    Args:
        text: 待检查文本。
        ignore_below: 绝对值小于该阈值的数字直接忽略（结构性数字，如「前 3 名」）。

    Returns:
        ``[(原始 token, 数值), ...]``，保持出现顺序。
    """
    out: List[Tuple[str, float]] = []
    for match in _NUMBER_PATTERN.finditer(text or ""):
        token = match.group(0)
        start, end = match.start(), match.end()
        # 形如 R03 的编号：前一个字符是字母或短横线 → 不是数值
        if start > 0 and (text[start - 1].isalpha() or text[start - 1] in "-_"):
            continue
        # 形如 2026-09 / 3-5 的日期或区间写法：后面紧跟「-数字」→ 不当作数值
        if end + 1 < len(text) and text[end] == "-" and text[end + 1].isdigit():
            continue
        value = _parse(token)
        if value is None or abs(value) < ignore_below:
            continue
        out.append((token, value))
    return out


def collect_allowed_numbers(payload: Any, *, include_percent: bool = True) -> List[float]:
    """递归收集证据表里的所有数值（含百分数形态）。"""
    found: List[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.append(float(node))
            if include_percent:
                found.append(float(node) * 100.0)
            return
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
            return
        if isinstance(node, (list, tuple, set)):
            for value in node:
                walk(value)

    walk(payload)
    return found


def is_bound(
    value: float,
    allowed: Sequence[float],
    *,
    rel_tol: float = 0.05,
    abs_tol: float = 1e-9,
) -> bool:
    """判断某个数字能否被证据表解释（允许四舍五入误差）。"""
    for candidate in allowed:
        tolerance = max(abs_tol, rel_tol * abs(candidate))
        if abs(value - candidate) <= tolerance:
            return True
    return False


def verify_numbers(
    text: str,
    payload: Any,
    *,
    rel_tol: float = 0.05,
    ignore_below: float = _DEFAULT_IGNORE_BELOW,
    extra_allowed: Iterable[float] = (),
) -> Dict[str, Any]:
    """校验文本中的数字是否都能回指证据表。

    Args:
        text: 待校验文本（通常是 AI 生成的结论）。
        payload: 证据表（任意嵌套结构，或已是数字序列）。
        rel_tol: 相对容差，默认 5%（容纳四舍五入）。
        ignore_below: 小于该绝对值的数字不校验（结构性数字）。
        extra_allowed: 额外允许的数值（如配置里的阈值）。

    Returns:
        ``{ok, checked, unbound:[{token, value}], allowed_count}``
    """
    allowed = collect_allowed_numbers(payload) if not isinstance(payload, (list, tuple)) or not all(
        isinstance(v, (int, float)) for v in payload
    ) else [float(v) for v in payload]
    allowed = list(allowed) + [float(v) for v in extra_allowed]

    numbers = extract_numbers(text, ignore_below=ignore_below)
    unbound = [
        {"token": token, "value": value}
        for token, value in numbers
        if not is_bound(value, allowed, rel_tol=rel_tol)
    ]
    return {
        "ok": not unbound,
        "checked": len(numbers),
        "unbound": unbound,
        "allowed_count": len(allowed),
    }


def guard_or_fallback(candidate: str, payload: Any, fallback: str, **kwargs: Any) -> Dict[str, Any]:
    """带回落的安全出口：候选文本过不了守卫就用确定性模板。

    Returns:
        ``{text, mode, guard}``；``mode`` 为 ``llm`` 或 ``template``（回落原因见 guard）。
    """
    guard = verify_numbers(candidate, payload, **kwargs)
    if guard["ok"] and (candidate or "").strip():
        return {"text": candidate, "mode": "llm", "guard": guard}
    guard["fallback_reason"] = (
        f"模型输出中有 {len(guard['unbound'])} 个数字无法回指证据表，已回落到确定性模板"
        if not guard["ok"] else "模型输出为空，已回落到确定性模板"
    )
    return {"text": fallback, "mode": "template", "guard": guard}
