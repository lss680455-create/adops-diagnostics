# -*- coding: utf-8 -*-
"""下载公开投放数据集（可选，仅用于把离线夹具重建到最新或做全量复算）。

仓库自带的夹具已经够跑通全流程；只有两种情况需要跑这个脚本：

1. 想用**全量**真实数据复算（16.5M 曝光，结论更稳）；
2. 想重新生成离线夹具（见 :mod:`scripts.build_fixture`）。

数据来源：Criteo Attribution Modeling for Bidding Dataset（公开研究数据集，
单次曝光级的真实投放日志，含 campaign / click / conversion / attribution / cost）。
许可：CC BY-NC-SA 4.0，非商用；引用方式见数据集 README。

用法::

    python scripts/fetch_public_data.py --out data/criteo_attribution.tsv.gz
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

DEFAULT_URL = (
    "https://huggingface.co/datasets/criteo/criteo-attribution-dataset"
    "/resolve/main/criteo_attribution_dataset.tsv.gz"
)


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path, *, force: bool = False) -> Path:
    """下载到 ``target``（已存在且非空时默认跳过）。"""
    if target.exists() and target.stat().st_size > 0 and not force:
        print(f"文件已存在，跳过下载：{target}（{target.stat().st_size:,} 字节）")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"开始下载：{url}")
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as handle:
        total = 0
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            handle.write(block)
            total += len(block)
            if total % (32 << 20) < (1 << 20):
                print(f"  已下载 {total / 1e6:.1f} MB", file=sys.stderr)
    print(f"下载完成：{target}（{target.stat().st_size:,} 字节）")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载 Criteo Attribution 公开数据集")
    parser.add_argument("--url", default=DEFAULT_URL, help="下载地址")
    parser.add_argument("--out", default="data/criteo_attribution.tsv.gz", help="保存路径")
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    parser.add_argument("--sha256", action="store_true", help="下载后打印校验值")
    args = parser.parse_args(argv)

    target = download(args.url, Path(args.out), force=args.force)
    if args.sha256:
        print(f"sha256: {_sha256(target)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
