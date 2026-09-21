# -*- coding: utf-8 -*-
"""测试公共夹具。

约定：**所有测试必须离线可跑**——不联网、不需要 API key、不读仓库外文件。
因此这里构造的都是小规模合成数据；真实公开数据的端到端验证放在
``test_pipeline.py`` 之外由 ``scripts/`` 手动跑（见 README）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
import pytest

from adops.config import DiagnoseConfig
from adops.diagnose.rules import DiagnosisInput
from adops.metrics.ads import build_metrics, cost_concentration
from adops.metrics.aggregate import (
    campaign_table,
    daily_totals,
    hourly_totals,
    overall_totals,
)

BASE_DATE = "2026-09-01"


def make_frame(rows: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    """构造规范的计数表（清洗层出口的口径）。

    每行是一个维度组合；缺失的计数列补 0，``attributed_conversions`` 缺省为
    ``conversions``（即完整归因）。
    """
    normalized: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        entry = {
            "date": row.get("date", BASE_DATE),
            "hour": row.get("hour", 12),
            "campaign_id": row.get("campaign_id", f"C{index + 1}"),
            "impressions": float(row.get("impressions", 0)),
            "clicks": float(row.get("clicks", 0)),
            "conversions": float(row.get("conversions", 0)),
            "cost": float(row.get("cost", 0.0)),
        }
        entry["attributed_conversions"] = float(
            row.get("attributed_conversions", entry["conversions"])
        )
        for extra in ("channel", "placement", "creative", "user_id"):
            if extra in row:
                entry[extra] = row[extra]
        normalized.append(entry)
    return pd.DataFrame(normalized)


def make_balanced_frame(
    *,
    days: int = 10,
    campaigns: int = 3,
    impressions: float = 10_000,
    ctr: float = 0.01,
    cvr: float = 0.10,
    cpm: float = 10.0,
    hour: int = 12,
) -> pd.DataFrame:
    """生成一份「干净」的投放数据：所有计划效率相同，不应触发任何效率类规则。"""
    rows: List[Dict[str, Any]] = []
    for day in range(1, days + 1):
        for campaign in range(1, campaigns + 1):
            clicks = impressions * ctr
            conversions = clicks * cvr
            cost = impressions / 1000.0 * cpm
            rows.append({
                "date": f"2026-09-{day:02d}",
                "hour": hour,
                "campaign_id": f"C{campaign}",
                "impressions": impressions,
                "clicks": clicks,
                "conversions": conversions,
                "cost": cost,
            })
    return make_frame(rows)


def make_diagnosis_input(
    frame: pd.DataFrame,
    *,
    cfg: Optional[DiagnoseConfig] = None,
    quality: Optional[Dict[str, Any]] = None,
    quality_failed: bool = False,
) -> DiagnosisInput:
    """由计数表组装诊断规则输入（口径与流水线一致）。"""
    campaigns = campaign_table(frame, top_n=50)
    totals = overall_totals(frame)
    metrics = build_metrics(totals)
    concentration = cost_concentration(
        {str(row["campaign_id"]): row["cost"] for row in campaigns.to_dict(orient="records")}
    )
    quality_payload = quality or {
        "checks": [{"check": "x", "status": "fail" if quality_failed else "ok",
                    "value": 0, "threshold": 0, "message": "test"}],
        "failed": 1 if quality_failed else 0,
        "warned": 0,
        "verdict": "test",
        "rows": int(len(frame)),
        "distinct_days": frame["date"].nunique() if "date" in frame.columns else 0,
        "layout": "impression",
        "time_basis": "absolute",
    }
    return DiagnosisInput(
        cfg=cfg or DiagnoseConfig(),
        totals=totals,
        metrics=metrics,
        campaign_table=campaigns,
        hourly=hourly_totals(frame),
        daily=daily_totals(frame),
        quality=quality_payload,
        concentration=concentration,
    )


@pytest.fixture()
def balanced_frame() -> pd.DataFrame:
    """干净数据：不应触发任何效率类规则。"""
    return make_balanced_frame()


@pytest.fixture()
def raw_impression_rows() -> List[Dict[str, Any]]:
    """原始曝光级日志（列名刻意用别名，覆盖映射逻辑）。"""
    return [
        {"timestamp": 0, "uid": 1, "campaign": 111, "click": 0, "conversion": 0,
         "attribution": 0, "cost": 1e-05},
        {"timestamp": 1, "uid": 2, "campaign": 111, "click": 1, "conversion": 0,
         "attribution": 0, "cost": 2e-05},
        {"timestamp": 3600, "uid": 3, "campaign": 222, "click": 1, "conversion": 1,
         "attribution": 1, "cost": 3e-05},
    ]


@pytest.fixture()
def raw_impression_frame(raw_impression_rows) -> pd.DataFrame:
    return pd.DataFrame(raw_impression_rows)


@pytest.fixture()
def raw_csv_factory():
    """返回一个「造投放日志 CSV」的工厂函数（供 CLI / 流水线测试使用）。"""
    import pandas as pd

    def factory(path):
        rows = []
        for day in range(1, 8):
            for campaign, base in (("111", 5000), ("222", 4000), ("333", 3000)):
                for index in range(20):
                    clicked = 1 if index < 2 else 0
                    rows.append({
                        "日期": f"2026-09-{day:02d}",
                        "小时": index % 24,
                        "广告计划": campaign,
                        "曝光": base // 20,
                        "点击": clicked,
                        "转化": 1 if (clicked and index == 0) else 0,
                        "消耗": 3.0 if campaign == "111" else 2.0,
                    })
        frame = pd.DataFrame(rows)
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    return factory
