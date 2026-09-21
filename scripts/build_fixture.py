# -*- coding: utf-8 -*-
"""从公开数据集生成仓库自带的离线夹具（可复现）。

生成两份夹具，都源自同一份真实曝光日志：

1. ``criteo_impression_sample.csv.gz`` —— 曝光级抽样（真实数据，逐行保留
   click / conversion / attribution / cost 标记），用来演示「曝光级日志 +
   列名别名 + 数据质量体检」这条链路；
2. ``criteo_campaign_hourly_sample.csv`` —— 由同一份数据聚合出的
   「计划 × 天 × 小时」中文报表（Top 50 计划），用来演示「聚合报表 + 中文列名」
   这条链路，也证明两条链路的指标口径一致。

注意：源数据集的时间戳是「距首条曝光经过的秒数」，不是日历时间。脚本据此
还原出「第 N 天 + 小时」，但**不会伪造具体日期**——报告里会标注时间口径为
``relative_day``。

用法::

    python scripts/build_fixture.py --input data/criteo_attribution.tsv.gz
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Tuple

SECONDS_PER_DAY = 86_400
IMPRESSION_COLUMNS = ("timestamp", "uid", "campaign", "conversion", "attribution",
                      "click", "cost", "cat1", "cat2")
REPORT_STATEMENT = (
    "本文件由 scripts/build_fixture.py 从公开数据集聚合生成，不含任何真实企业投放数据。"
)


def _open_maybe_gzip(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("rt", encoding="utf-8", newline="")


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def build(input_path: Path, out_dir: Path, *, sample_every: int = 55, top_campaigns: int = 50) -> Dict[str, object]:
    """流式处理原始文件，产出两份夹具与元信息。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    impression_path = out_dir / "criteo_impression_sample.csv.gz"
    report_path = out_dir / "criteo_campaign_hourly_sample.csv"

    campaign_cost: Dict[str, float] = {}
    cells: Dict[Tuple[int, int, str], List[float]] = {}
    sampled: List[str] = []
    rows = 0
    malformed = 0

    with _open_maybe_gzip(input_path) as handle:
        header = handle.readline()
        if not header:
            raise SystemExit(f"输入文件为空：{input_path}")
        for line in handle:
            rows += 1
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 13:
                malformed += 1
                continue
            try:
                timestamp = int(parts[0])
                campaign = parts[2]
                conversion = int(parts[3])
                attribution = int(parts[6])
                click = int(parts[7])
                cost = float(parts[10])
            except ValueError:
                malformed += 1
                continue

            day = timestamp // SECONDS_PER_DAY + 1
            hour = (timestamp % SECONDS_PER_DAY) // 3600

            campaign_cost[campaign] = campaign_cost.get(campaign, 0.0) + cost
            key = (day, hour, campaign)
            cell = cells.get(key)
            if cell is None:
                cells[key] = [1.0, float(click), float(conversion), float(attribution), cost]
            else:
                cell[0] += 1.0
                cell[1] += click
                cell[2] += conversion
                cell[3] += attribution
                cell[4] += cost

            if rows % sample_every == 0:
                sampled.append(
                    "\t".join([str(timestamp), parts[1], campaign, str(conversion),
                               str(attribution), str(click), parts[10], parts[13],
                               parts[14] if len(parts) > 14 else ""])
                )

    top = [name for name, _ in sorted(campaign_cost.items(), key=lambda kv: -kv[1])[:top_campaigns]]
    top_set = set(top)

    with gzip.open(impression_path, "wt", encoding="utf-8", newline="") as handle:
        handle.write(",".join(IMPRESSION_COLUMNS) + "\n")
        for line in sampled:
            fields = line.split("\t")
            handle.write(",".join(fields) + "\n")

    report_rows = sorted(
        ((day, hour, campaign, value) for (day, hour, campaign), value in cells.items()
         if campaign in top_set),
        key=lambda item: (item[0], item[1], item[2]),
    )
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("日期,小时,广告计划,曝光量,点击量,转化数,归因转化数,消耗\n")
        for day, hour, campaign, value in report_rows:
            handle.write(
                f"D{day:03d},{hour},{campaign},{int(value[0])},{int(value[1])},"
                f"{int(value[2])},{int(value[3])},{value[4]:.10f}\n"
            )

    meta = {
        "source": "Criteo Attribution Modeling for Bidding Dataset",
        "source_license": "CC BY-NC-SA 4.0（非商用）",
        "source_file": input_path.name,
        "source_sha256": _sha256(input_path),
        "source_rows": rows,
        "malformed_rows": malformed,
        "sample_every": sample_every,
        "impression_fixture_rows": len(sampled),
        "report_fixture_rows": len(report_rows),
        "top_campaigns": top_campaigns,
        "time_basis": "relative_day（源数据为距首条曝光的秒数，未伪造日历日期）",
        "statement": REPORT_STATEMENT,
    }
    (out_dir / "fixture_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成离线夹具")
    parser.add_argument("--input", default="data/criteo_attribution.tsv.gz", help="原始 tsv.gz 路径")
    parser.add_argument("--out-dir", default="examples/sample_data", help="夹具输出目录")
    parser.add_argument("--sample-every", type=int, default=55, help="曝光级抽样的间隔（每 N 行取 1 行）")
    parser.add_argument("--top-campaigns", type=int, default=50, help="聚合报表保留的计划数")
    args = parser.parse_args(argv)

    meta = build(Path(args.input), Path(args.out_dir),
                 sample_every=args.sample_every, top_campaigns=args.top_campaigns)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
