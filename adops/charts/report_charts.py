# -*- coding: utf-8 -*-
"""图表层：六张标准图，全部由聚合结果渲染，无交互依赖（Agg 后端）。

图表的取舍标准是**能不能支持决策**，不是好不好看：

1. 计划「消耗 × CPA」双信息条形 —— 一眼看出哪些计划在花大钱且效率差；
2. CTR × CVR 象限散点 —— 把「素材问题」和「承接问题」分开（这两类问题的
   处理人不同，混在一起就会互相甩锅）；
3. 漏斗 —— 定位断点在哪一层；
4. 分时 CTR / CPA 双轴 —— 找可分时加价的机会；
5. 消耗洛伦兹曲线 —— 集中度风险的直观表达；
6. 日趋势 —— 消耗与 CPA 的走向，判断是否在恶化。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from ..config import ChartConfig
from ..util import safe_float

_LABELS: Dict[str, Dict[str, str]] = {
    "zh": {
        "spend_vs_cpa": "计划消耗与 CPA（前 {n} 名，按消耗降序）",
        "spend": "消耗",
        "cpa": "CPA",
        "ctr_cvr": "计划四象限：CTR × CVR（虚线为全局均值）",
        "ctr": "CTR",
        "cvr": "CVR",
        "funnel": "投放漏斗与环节转化率",
        "hourly": "分时 CTR 与 CPA",
        "hour": "小时",
        "lorenz": "消耗集中度（洛伦兹曲线，HHI = {hhi}）",
        "cum_share_campaigns": "计划累计占比",
        "cum_share_cost": "消耗累计占比",
        "daily": "日消耗与 CPA 趋势",
        "date": "日期",
        "cost": "消耗",
        "conversions": "转化数",
    },
    "en": {
        "spend_vs_cpa": "Spend vs CPA by campaign (top {n} by spend)",
        "spend": "Spend",
        "cpa": "CPA",
        "ctr_cvr": "Campaign quadrants: CTR vs CVR (dashed = account mean)",
        "ctr": "CTR",
        "cvr": "CVR",
        "funnel": "Delivery funnel and step conversion",
        "hourly": "CTR and CPA by hour of day",
        "hour": "Hour",
        "lorenz": "Spend concentration (Lorenz curve, HHI = {hhi})",
        "cum_share_campaigns": "Cumulative share of campaigns",
        "cum_share_cost": "Cumulative share of spend",
        "daily": "Daily spend and CPA trend",
        "date": "Date",
        "cost": "Spend",
        "conversions": "Conversions",
    },
}


def _setup_style(cfg: ChartConfig) -> str:
    """配置 matplotlib；找不到中文字体时自动降级为英文标签。

    Returns:
        实际使用的语言（``zh`` / ``en``）。
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["figure.dpi"] = cfg.dpi
    plt.rcParams["savefig.bbox"] = "tight"
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.25
    plt.rcParams["axes.spines.top"] = False
    plt.rcParams["axes.spines.right"] = False

    if cfg.lang != "zh":
        return "en"
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    for candidate in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC",
                      "PingFang SC", "WenQuanYi Zen Hei"):
        if candidate in installed:
            plt.rcParams["font.sans-serif"] = [candidate, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return "zh"
    return "en"


def _fig(lang: str, cfg: ChartConfig):
    import matplotlib.pyplot as plt

    return plt.subplots(figsize=(cfg.width, cfg.height))


def render_all(
    metrics: Dict[str, Any],
    campaign_table: pd.DataFrame,
    hourly: pd.DataFrame,
    daily: pd.DataFrame,
    concentration: Dict[str, Any],
    out_dir: str | Path,
    *,
    cfg: Optional[ChartConfig] = None,
    top_n: int = 10,
) -> Dict[str, Path]:
    """渲染全部图表。

    Returns:
        ``{图表名: 文件路径}``。
    """
    cfg = cfg or ChartConfig()
    lang = _setup_style(cfg)
    labels = _LABELS[lang]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: Dict[str, Path] = {}
    renderers = (
        ("campaign_spend_vs_cpa", lambda: _chart_spend_vs_cpa(campaign_table, metrics, labels, cfg, top_n)),
        ("campaign_quadrants", lambda: _chart_quadrants(campaign_table, metrics, labels, cfg)),
        ("funnel", lambda: _chart_funnel(metrics, labels, cfg)),
        ("hourly_ctr_cpa", lambda: _chart_hourly(hourly, labels, cfg)),
        ("spend_concentration", lambda: _chart_lorenz(campaign_table, concentration, labels, cfg)),
        ("daily_trend", lambda: _chart_daily(daily, labels, cfg)),
    )
    for name, render in renderers:
        try:
            figure = render()
        except Exception:  # 单张图失败不拖垮整份报告
            continue
        if figure is None:
            continue
        target = out_dir / f"{name}.png"
        figure.savefig(target)
        import matplotlib.pyplot as plt

        plt.close(figure)
        paths[name] = target
    return paths


def _chart_spend_vs_cpa(
    table: pd.DataFrame, metrics: Dict[str, Any], labels: Dict[str, str], cfg: ChartConfig, top_n: int
):
    if table is None or table.empty:
        return None
    data = table.head(top_n).iloc[::-1]
    names = [str(v) for v in data["campaign_id"]]
    spend = [safe_float(v) for v in data["cost"]]
    cpa_values = data["cpa"].tolist()

    figure, axis = _fig(_language(labels), cfg)
    axis.barh(names, spend, color="#4C78A8", alpha=0.85, label=labels["spend"])
    axis.set_xlabel(labels["spend"])
    axis.set_title(labels["spend_vs_cpa"].format(n=len(names)), fontsize=11)
    for index, value in enumerate(cpa_values):
        text = "—" if value is None or pd.isna(value) else f"{float(value):,.4f}"
        axis.text(safe_float(spend[index]) * 0.02, index, f"CPA {text}", va="center", fontsize=8,
                  color="#B03A2E")
    return figure


def _chart_quadrants(table: pd.DataFrame, metrics: Dict[str, Any], labels: Dict[str, str], cfg: ChartConfig):
    if table is None or table.empty:
        return None
    data = table.dropna(subset=["ctr", "cvr"])
    if data.empty:
        return None
    figure, axis = _fig(_language(labels), cfg)
    sizes = [max(20.0, safe_float(v) * 3000.0) for v in data["spend_share"]]
    axis.scatter(data["ctr"], data["cvr"], s=sizes, alpha=0.6, color="#5B8FF9", edgecolor="white")
    mean_ctr = metrics.get("derived", {}).get("ctr")
    mean_cvr = metrics.get("derived", {}).get("cvr")
    if mean_ctr:
        axis.axvline(mean_ctr, linestyle="--", color="#999999", linewidth=1)
    if mean_cvr:
        axis.axhline(mean_cvr, linestyle="--", color="#999999", linewidth=1)
    axis.set_xlabel(labels["ctr"])
    axis.set_ylabel(labels["cvr"])
    axis.set_title(labels["ctr_cvr"], fontsize=11)
    return figure


def _chart_funnel(metrics: Dict[str, Any], labels: Dict[str, str], cfg: ChartConfig):
    funnel = metrics.get("funnel") or []
    if not funnel:
        return None
    figure, axis = _fig(_language(labels), cfg)
    names = [step["label"] for step in funnel]
    values = [safe_float(step["value"]) for step in funnel]
    axis.bar(names, values, color=["#4C78A8", "#F58518", "#54A24B"][:len(names)], alpha=0.9)
    for index, step in enumerate(funnel):
        rate = step.get("step_display")
        if index > 0 and rate:
            axis.text(index, values[index], f"环节转化 {rate}", ha="center", va="bottom", fontsize=8)
    axis.set_title(labels["funnel"], fontsize=11)
    axis.set_ylabel(labels["conversions"])
    return figure


def _chart_hourly(hourly: pd.DataFrame, labels: Dict[str, str], cfg: ChartConfig):
    if hourly is None or hourly.empty:
        return None
    figure, axis = _fig(_language(labels), cfg)
    axis.plot(hourly["hour"], hourly["ctr"].astype(float), marker="o", color="#4C78A8",
              label=labels["ctr"])
    axis.set_xlabel(labels["hour"])
    axis.set_ylabel(labels["ctr"])
    twin = axis.twinx()
    twin.plot(hourly["hour"], hourly["cpa"].astype(float), marker="s", color="#E45756",
              label=labels["cpa"])
    twin.set_ylabel(labels["cpa"])
    twin.grid(False)
    axis.set_title(labels["hourly"], fontsize=11)
    return figure


def _chart_lorenz(table: pd.DataFrame, concentration: Dict[str, Any], labels: Dict[str, str],
                  cfg: ChartConfig):
    if table is None or table.empty:
        return None
    spends = sorted((safe_float(v) for v in table["cost"]), reverse=True)
    total = sum(spends)
    if total <= 0:
        return None
    cumulative = []
    running = 0.0
    for value in spends:
        running += value
        cumulative.append(running / total)
    x = [(i + 1) / len(spends) for i in range(len(spends))]

    figure, axis = _fig(_language(labels), cfg)
    axis.plot([0.0] + x, [0.0] + cumulative, color="#4C78A8", label=labels["cum_share_cost"])
    axis.plot([0.0, 1.0], [0.0, 1.0], linestyle="--", color="#999999", label="完全平均")
    axis.set_xlabel(labels["cum_share_campaigns"])
    axis.set_ylabel(labels["cum_share_cost"])
    hhi_value = concentration.get("hhi")
    axis.set_title(
        labels["lorenz"].format(hhi="—" if hhi_value is None else f"{float(hhi_value):.4f}"),
        fontsize=11,
    )
    axis.legend(fontsize=8)
    return figure


def _chart_daily(daily: pd.DataFrame, labels: Dict[str, str], cfg: ChartConfig):
    if daily is None or daily.empty:
        return None
    figure, axis = _fig(_language(labels), cfg)
    axis.bar(daily["date"].astype(str), daily["cost"].astype(float), color="#4C78A8",
             alpha=0.75, label=labels["cost"])
    axis.set_xlabel(labels["date"])
    axis.set_ylabel(labels["cost"])
    axis.tick_params(axis="x", rotation=90, labelsize=7)
    twin = axis.twinx()
    twin.plot(daily["date"].astype(str), daily["cpa"].astype(float), marker="o",
              color="#E45756", linewidth=1.5, label=labels["cpa"])
    twin.set_ylabel(labels["cpa"])
    twin.grid(False)
    axis.set_title(labels["daily"], fontsize=11)
    return figure


def _language(labels: Dict[str, str]) -> str:
    """由标签表反推语言，供 _fig 使用（避免到处传 lang）。"""
    return "zh" if labels.get("spend") == "消耗" else "en"
