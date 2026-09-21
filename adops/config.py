# -*- coding: utf-8 -*-
"""集中配置：YAML 文件 + 环境变量，无硬编码密钥、无本机绝对路径。

设计沿用「配置集中、路径相对仓库根」的约定：所有阈值（诊断规则、质量门禁、
实验参数）都从这里读取，代码里不出现魔法数字——阈值改了要能追溯到一处。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

try:  # python-dotenv 为可选依赖
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - 仅缺依赖时触发
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "default.yaml"


@dataclass(frozen=True)
class RunConfig:
    """流水线运行参数。"""

    account: str = "demo-account"
    source: str = "sample"  # sample / csv / criteo
    data_file: str = "examples/sample_data/criteo_attribution_sample.csv.gz"
    layout: str = "auto"  # auto / impression / aggregated
    out_dir: str = "outputs"
    top_n: int = 10
    min_days: int = 1


@dataclass(frozen=True)
class QualityConfig:
    """数据质量体检门禁。"""

    max_missing_rate: float = 0.05
    max_duplicate_rate: float = 0.01
    cost_outlier_iqr: float = 3.0
    zero_cost_alert_rate: float = 0.30


@dataclass(frozen=True)
class DiagnoseConfig:
    """诊断规则阈值。

    所有阈值都要求「样本量达标」才允许报警——小样本下的比率差是噪声，
    宁可漏报也不误报（阈值宁高勿低）。
    """

    min_impressions: int = 1000
    min_clicks: int = 30
    min_conversions: int = 10
    cpa_multiplier: float = 1.5
    spend_share_floor: float = 0.05
    hhi_alert: float = 0.25
    top1_share_alert: float = 0.40
    no_conversion_spend_share: float = 0.03
    cpm_low: float = 0.5
    cpm_high: float = 2.0
    ctr_low: float = 0.5
    trend_lookback_days: int = 7
    trend_recent_days: int = 3
    trend_cpa_rise: float = 0.30
    attribution_floor: float = 0.80
    z_alpha: float = 0.05


@dataclass(frozen=True)
class ExperimentConfig:
    """实验参数默认值。"""

    alpha: float = 0.05
    power: float = 0.80
    mde_rel: float = 0.10
    baseline_metric: str = "ctr"
    min_daily_impressions: int = 0
    looks: int = 1


@dataclass(frozen=True)
class ChartConfig:
    """图表导出参数。"""

    dpi: int = 150
    lang: str = "zh"  # zh / en，中文字体缺失时自动降级为 en
    width: float = 9.0
    height: float = 4.6


@dataclass(frozen=True)
class LLMConfig:
    """LLM（OpenAI 兼容接口）配置；未配置密钥时自动禁用。"""

    enabled: bool = False
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    max_tokens: int = 1200
    timeout: float = 60.0

    @property
    def usable(self) -> bool:
        """是否具备可调用的最小条件（开关打开且密钥存在）。"""
        return bool(self.enabled and self.api_key)


@dataclass(frozen=True)
class AppConfig:
    """应用总配置。"""

    run: RunConfig = field(default_factory=RunConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    diagnose: DiagnoseConfig = field(default_factory=DiagnoseConfig)
    experiment: ExperimentConfig = field(default_factory=ExperimentConfig)
    charts: ChartConfig = field(default_factory=ChartConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    root: Path = PROJECT_ROOT

    def resolve(self, path: str | Path) -> Path:
        """把配置中的相对路径解析为仓库根下的绝对路径。"""
        p = Path(path)
        return p if p.is_absolute() else (self.root / p)


_SECTIONS = ("run", "quality", "diagnose", "experiment", "charts", "llm")


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"配置节 `{key}` 必须是映射（dict），实际为 {type(value).__name__}")
    return value


def _truthy(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config(
    path: Optional[str | Path] = None,
    *,
    load_env: bool = True,
    **overrides: dict[str, Any],
) -> AppConfig:
    """读取 YAML 配置并叠加环境变量覆盖。

    Args:
        path: 配置文件路径；None 表示 ``configs/default.yaml``（缺失则用默认值）。
        load_env: 是否从 ``.env`` 加载环境变量。
        **overrides: 形如 ``run={"source": "csv"}`` 的分节覆盖。

    Returns:
        AppConfig: 冻结的应用配置对象。
    """
    if load_env and load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")

    raw: dict[str, Any] = {}
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"配置文件 {cfg_path} 顶层必须是映射（dict）")

    buckets: dict[str, dict[str, Any]] = {name: dict(_section(raw, name)) for name in _SECTIONS}
    for key, section in overrides.items():
        if key not in buckets:
            raise KeyError(f"未知配置节: {key}")
        if not isinstance(section, dict):
            raise ValueError(f"覆盖节 `{key}` 必须是映射（dict）")
        buckets[key].update({k: v for k, v in section.items() if v is not None})

    # ---- 环境变量覆盖（环境优先于文件）----
    env_run = {
        "account": os.getenv("ADOPS_ACCOUNT"),
        "source": os.getenv("ADOPS_SOURCE"),
        "data_file": os.getenv("ADOPS_DATA_FILE"),
        "layout": os.getenv("ADOPS_LAYOUT"),
        "out_dir": os.getenv("ADOPS_OUT_DIR"),
    }
    buckets["run"].update({k: v for k, v in env_run.items() if v})

    env_llm = {
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "base_url": os.getenv("OPENAI_BASE_URL", ""),
        "model": os.getenv("OPENAI_MODEL", ""),
    }
    buckets["llm"].update({k: v for k, v in env_llm.items() if v})

    env_llm_enabled = _truthy(os.getenv("ADOPS_LLM"))
    if env_llm_enabled is not None:
        buckets["llm"]["enabled"] = env_llm_enabled
    elif buckets["llm"].get("api_key"):
        buckets["llm"].setdefault("enabled", False)

    if buckets["llm"].get("enabled") and not buckets["llm"].get("api_key"):
        buckets["llm"]["enabled"] = False  # 开启但无密钥 → 自动禁用（降级而非报错）

    return AppConfig(
        run=RunConfig(**buckets["run"]),
        quality=QualityConfig(**buckets["quality"]),
        diagnose=DiagnoseConfig(**buckets["diagnose"]),
        experiment=ExperimentConfig(**buckets["experiment"]),
        charts=ChartConfig(**buckets["charts"]),
        llm=LLMConfig(**buckets["llm"]),
    )
