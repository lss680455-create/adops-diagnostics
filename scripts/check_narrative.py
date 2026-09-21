# -*- coding: utf-8 -*-
"""CI 守卫脚本：断言报告结论里没有「无法回指证据表」的数字。

为什么把它放进 CI：数字回指是这条流水线对「AI 参与生产」的核心承诺，
如果只在文档里写，某次改动就可能悄悄绕过守卫。放进 CI 之后，
「AI 有没有编数字」变成了一条会失败的检查。

用法::

    python scripts/check_narrative.py outputs/ci/narrative.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def check(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    guard = payload.get("guard") or {}
    unbound = guard.get("unbound") or []
    mode = payload.get("mode")
    if unbound:
        print(f"❌ 结论中存在 {len(unbound)} 个无法回指证据表的数字：{unbound}", file=sys.stderr)
        return 1
    text = payload.get("text") or ""
    if not text.strip():
        print("❌ 结论为空", file=sys.stderr)
        return 1
    print(f"✅ 叙述模式 {mode}｜校验数字 {guard.get('checked', 0)} 个，全部可回指证据表")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    target = Path(args[0]) if args else Path("outputs/ci/narrative.json")
    if not target.exists():
        print(f"文件不存在：{target}", file=sys.stderr)
        return 2
    return check(target)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
