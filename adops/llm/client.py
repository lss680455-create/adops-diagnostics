# -*- coding: utf-8 -*-
"""LLM 客户端：OpenAI 兼容接口，仅用标准库实现。

刻意不做的事：不重试到天亮、不吞异常。调用失败由上层的叙述层捕获并降级到
确定性模板——**报告永远能出**，这也是这条流水线的底线。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..config import LLMConfig


class LLMError(RuntimeError):
    """LLM 调用失败。"""


class LLMClient:
    """极简的 chat/completions 客户端。"""

    def __init__(self, cfg: LLMConfig):
        if not cfg.usable:
            raise LLMError("LLM 未启用或缺少 api_key")
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")

    def complete(self, system_prompt: str, user_prompt: str, *, purpose: str = "generic") -> str:
        """调用一次补全，返回纯文本。

        Args:
            system_prompt: 系统提示（约束写在里面）。
            user_prompt: 用户输入（证据表渲染后的文本）。
            purpose: 调用用途，仅用于日志与排障。

        Raises:
            LLMError: 网络失败、HTTP 非 2xx 或返回体缺字段。
        """
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.cfg.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - 网络分支
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise LLMError(f"[{purpose}] HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:  # pragma: no cover
            raise LLMError(f"[{purpose}] 网络错误：{exc}") from exc

        choices: List[Dict[str, Any]] = body.get("choices") or []
        if not choices:
            raise LLMError(f"[{purpose}] 返回体缺少 choices：{str(body)[:300]}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMError(f"[{purpose}] 返回内容不是文本：{str(content)[:200]}")
        return content.strip()

    def analyze_metrics(self, account: str, metrics_text: str) -> str:
        """便利方法：给指标表写一段解读（供 CLI 单点调用）。"""
        system = (
            "你是一名广告投放分析师。只能使用用户给出的数字，"
            "不得计算或补充任何新数字；不确定就明说样本不足。输出中文，200 字以内。"
        )
        user = f"账户：{account}\n以下是指标：\n{metrics_text}"
        return self.complete(system, user, purpose="metrics")


def available(cfg: Optional[LLMConfig]) -> bool:
    """对外暴露的可用性判断（供上层决定走模板还是走模型）。"""
    return bool(cfg and cfg.usable)
