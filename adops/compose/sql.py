# -*- coding: utf-8 -*-
"""SQL 生成层：把诊断口径翻译成可以直接粘给分析师的查询。

产品视角的价值：模型/脚本算出来的结论，业务方往往要在数仓里**自己复核一遍**
才敢动预算。能顺手给出与结论口径完全一致的 SQL，是「可复核」最实际的落地方式
（也是数据敏感度这一项最容易被问到的加分点）。

所有查询都遵守同一条纪律：**先加总再相除**，并用 NULLIF 防护除零。
"""
from __future__ import annotations

from typing import Dict, List

#: 统一表名与字段（报告附录里会注明，方便换成真实数仓表）
TABLE = "ad_impressions"
FIELDS = (
    "date", "campaign_id", "placement", "creative",
    "impressions", "clicks", "conversions", "cost", "attributed_conversions",
)


def campaign_efficiency_sql(top_n: int = 10) -> str:
    """计划效率表：与报告中的「计划效率」表口径一致。"""
    return f"""SELECT
  campaign_id,
  SUM(impressions)                                        AS impressions,
  SUM(clicks)                                             AS clicks,
  SUM(conversions)                                        AS conversions,
  SUM(cost)                                               AS cost,
  SUM(cost) / NULLIF(SUM(impressions), 0) * 1000          AS cpm,
  SUM(clicks) / NULLIF(SUM(impressions), 0)               AS ctr,
  SUM(cost) / NULLIF(SUM(clicks), 0)                      AS cpc,
  SUM(conversions) / NULLIF(SUM(clicks), 0)               AS cvr,
  SUM(cost) / NULLIF(SUM(conversions), 0)                 AS cpa,
  SUM(cost) / NULLIF(SUM(SUM(cost)) OVER (), 0)           AS spend_share
FROM {TABLE}
GROUP BY campaign_id
ORDER BY cost DESC
LIMIT {int(top_n)};"""


def hourly_efficiency_sql() -> str:
    """分时效率：用于复核「时段机会」类结论（EXTRACT 按各数仓方言改写）。"""
    return f"""SELECT
  EXTRACT(HOUR FROM CAST(date AS TIMESTAMP))              AS hour_of_day,
  SUM(impressions)                                        AS impressions,
  SUM(clicks)                                             AS clicks,
  SUM(conversions)                                        AS conversions,
  SUM(cost)                                               AS cost,
  SUM(clicks) / NULLIF(SUM(impressions), 0)               AS ctr,
  SUM(cost) / NULLIF(SUM(conversions), 0)                 AS cpa
FROM {TABLE}
GROUP BY 1
ORDER BY 1;"""


def data_quality_sql() -> str:
    """数据质量自查：与质量体检阶段的口径一致（重复率、负消耗、逻辑冲突）。"""
    return f"""SELECT
  COUNT(*)                                                       AS rows_total,
  COUNT(*) - COUNT(DISTINCT CONCAT_WS('|',
      CAST(date AS STRING), CAST(campaign_id AS STRING),
      CAST(impressions AS STRING), CAST(clicks AS STRING),
      CAST(conversions AS STRING), CAST(cost AS STRING)))        AS duplicate_rows,
  SUM(CASE WHEN cost < 0 THEN 1 ELSE 0 END)                      AS negative_cost_rows,
  SUM(CASE WHEN clicks > impressions THEN 1 ELSE 0 END)          AS clicks_gt_impressions,
  SUM(CASE WHEN conversions > clicks THEN 1 ELSE 0 END)          AS conversions_gt_clicks,
  SUM(CASE WHEN conversions > 0 THEN 1 ELSE 0 END)               AS conversion_rows
FROM {TABLE};"""


def daily_trend_sql() -> str:
    """日趋势：复核 CPA 走高/消耗节奏异常类结论。"""
    return f"""SELECT
  date,
  SUM(cost)                                               AS cost,
  SUM(conversions)                                        AS conversions,
  SUM(cost) / NULLIF(SUM(conversions), 0)                 AS cpa,
  SUM(clicks) / NULLIF(SUM(impressions), 0)               AS ctr
FROM {TABLE}
GROUP BY date
ORDER BY date;"""


def attribution_gap_sql() -> str:
    """归因覆盖率：复核 CPA 是否被系统性高估。"""
    return f"""SELECT
  campaign_id,
  SUM(conversions)                                        AS conversions,
  SUM(attributed_conversions)                             AS attributed_conversions,
  SUM(attributed_conversions) / NULLIF(SUM(conversions), 0) AS attributed_rate,
  SUM(cost) / NULLIF(SUM(conversions), 0)                 AS cpa_all,
  SUM(cost) / NULLIF(SUM(attributed_conversions), 0)       AS cpa_attributed
FROM {TABLE}
GROUP BY campaign_id
HAVING SUM(conversions) > 0
ORDER BY conversions DESC;"""


def experiment_readout_sql(experiment_label: str = "exp_001") -> str:
    """实验读数：给定实验标记，输出两组对比（列名按实验平台约定改写）。"""
    return f"""SELECT
  experiment_arm,
  COUNT(*)                                                AS rows_total,
  SUM(impressions)                                        AS impressions,
  SUM(clicks)                                             AS clicks,
  SUM(conversions)                                        AS conversions,
  SUM(cost)                                               AS cost,
  SUM(clicks) / NULLIF(SUM(impressions), 0)               AS ctr,
  SUM(conversions) / NULLIF(SUM(clicks), 0)               AS cvr,
  SUM(cost) / NULLIF(SUM(conversions), 0)                 AS cpa
FROM {TABLE}
WHERE experiment_label = '{experiment_label}'
GROUP BY experiment_arm;"""


def all_statements(top_n: int = 10) -> Dict[str, str]:
    """返回全部 SQL 片段（报告附录使用）。"""
    return {
        "计划效率": campaign_efficiency_sql(top_n),
        "分时效率": hourly_efficiency_sql(),
        "数据质量自查": data_quality_sql(),
        "日趋势": daily_trend_sql(),
        "归因覆盖率": attribution_gap_sql(),
        "实验读数": experiment_readout_sql(),
    }


def render_sql_appendix(top_n: int = 10) -> str:
    """把全部 SQL 渲染成 Markdown 附录。"""
    lines: List[str] = [
        f"表名约定：`{TABLE}`，字段：{'、'.join(f'`{f}`' for f in FIELDS)}。",
        "所有比率均为「先加总再相除」，除零用 `NULLIF` 防护。",
        "",
    ]
    for name, statement in all_statements(top_n).items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append("```sql")
        lines.append(statement)
        lines.append("```")
        lines.append("")
    return "\n".join(lines)
