# -*- coding: utf-8 -*-
"""规范字段与列名别名映射。

投放日志的列名是最容易出错的一环：同一个「消耗」在巨量引擎导出里叫
``stat_cost``、在腾讯广告里叫 ``cost``、在人工整理的表里叫「花费」。
这一层把「来源列名」收敛到一套规范字段，让下游指标代码只认规范名。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

#: 规范字段 → 中文名（报告与图表用）
CANONICAL_FIELDS: Dict[str, str] = {
    "impressions": "曝光量",
    "clicks": "点击量",
    "conversions": "转化数",
    "cost": "消耗",
    "attributed_conversions": "归因转化数",
    "campaign_id": "广告计划",
    "channel": "媒体/渠道",
    "placement": "广告位",
    "creative": "素材",
    "date": "日期",
    "hour": "小时",
    "user_id": "用户标识",
    "click_nb": "点击次数",
    "time_since_last_click": "距上次点击秒数",
}

#: 别名 → 规范字段（全部小写比对，去掉空格与下划线后匹配）
ALIASES: Dict[str, str] = {
    # 曝光
    "impressions": "impressions", "impression": "impressions", "imps": "impressions",
    "imprs": "impressions", "impr": "impressions", "show": "impressions", "shows": "impressions",
    "view": "impressions", "views": "impressions", "曝光": "impressions",
    "曝光量": "impressions", "展示": "impressions", "展示量": "impressions",
    "impressioncount": "impressions",
    # 点击
    "clicks": "clicks", "click": "clicks", "clk": "clicks", "点击": "clicks",
    "点击量": "clicks", "clickcount": "clicks",
    # 转化
    "conversions": "conversions", "conversion": "conversions", "conv": "conversions",
    "orders": "conversions", "order": "conversions", "转化": "conversions",
    "转化数": "conversions", "转化量": "conversions", "conversioncount": "conversions",
    # 消耗
    "cost": "cost", "spend": "cost", "spending": "cost", "amount": "cost",
    "expense": "cost", "消耗": "cost", "花费": "cost", "成本": "cost",
    "消耗金额": "cost", "statcost": "cost", "totalcost": "cost",
    # 归因转化
    "attributed": "attributed_conversions", "attributedconversions": "attributed_conversions",
    "attributedconversion": "attributed_conversions", "attribution": "attributed_conversions",
    "归因转化": "attributed_conversions", "归因转化数": "attributed_conversions",
    # 计划 / 渠道 / 位置 / 素材
    "campaign": "campaign_id", "campaignid": "campaign_id", "campaignname": "campaign_id",
    "adgroup": "campaign_id", "adgroupid": "campaign_id", "plan": "campaign_id",
    "计划": "campaign_id", "广告计划": "campaign_id", "计划id": "campaign_id",
    "channel": "channel", "media": "channel", "mediachannel": "channel",
    "渠道": "channel", "媒体": "channel",
    "placement": "placement", "slot": "placement", "position": "placement",
    "adslot": "placement", "bannerpos": "placement",
    "广告位": "placement", "位置": "placement", "广告位置": "placement",
    "creative": "creative", "creativeid": "creative", "material": "creative",
    "素材": "creative", "素材id": "creative",
    # 时间
    "date": "date", "day": "date", "dt": "date", "statdate": "date",
    "日期": "date", "stat_time": "date",
    "hour": "hour", "hourofday": "hour", "hourid": "hour", "小时": "hour",
    "timestamp": "date", "time": "date",
    # 用户与点击行为
    "uid": "user_id", "userid": "user_id", "user": "user_id", "用户": "user_id",
    "clicknb": "click_nb", "clicknum": "click_nb", "clickscount": "click_nb",
    "timesincelastclick": "time_since_last_click",
}

#: 指标（率）计算所需的最小字段集
REQUIRED_FIELDS: Tuple[str, ...] = ("impressions", "clicks")

#: 判断「已经是聚合表」的字段（出现任一即认为输入是聚合报表，不是曝光级日志）
AGGREGATED_MARKERS: Tuple[str, ...] = ("impressions", "clicks")


def _normalize_key(name: str) -> str:
    """列名归一化：小写、去掉空格/下划线/连字符/点。"""
    return "".join(ch for ch in str(name).strip().lower() if ch not in " _-.\t")


def resolve_column(name: str) -> str | None:
    """把单个来源列名解析为规范字段名；无法识别返回 None。"""
    return ALIASES.get(_normalize_key(name))


def map_columns(columns: Iterable[str]) -> Tuple[Dict[str, str], List[str]]:
    """批量解析列名。

    Returns:
        ``(rename_map, unmapped)``：``rename_map`` 为 ``{原列名: 规范字段}``，
        ``unmapped`` 为无法识别的原列名（保留为上下文特征 cat*，不参与指标计算）。
    """
    rename: Dict[str, str] = {}
    unmapped: List[str] = []
    for column in columns:
        target = resolve_column(column)
        if target is None:
            unmapped.append(str(column))
        else:
            # 同一规范字段被两列命中时，先到者优先（避免覆盖已确认的映射）
            if target in rename.values():
                unmapped.append(str(column))
                continue
            rename[str(column)] = target
    return rename, unmapped


def detect_layout(columns: Iterable[str]) -> str:
    """判断输入是「曝光级日志」还是「聚合报表」。

    曝光级日志每行是一次曝光，带 click / conversion 的 0-1 标记；
    聚合报表每行已含曝光量/点击量计数。两者下游走同一套指标代码，
    但清洗与样本量口径不同，必须在入口处判定。
    """
    normalized = {_normalize_key(c) for c in columns}
    has_click_flag = "click" in normalized or bool(normalized & {"click", "clk"})
    has_count = "impressions" in normalized or "imps" in normalized
    if has_click_flag and not has_count:
        return "impression"
    if has_count:
        return "aggregated"
    if has_click_flag:
        return "impression"
    return "aggregated"
