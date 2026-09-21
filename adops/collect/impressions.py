# -*- coding: utf-8 -*-
"""取数层：把不同来源的投放数据读成同一张原始表。

三个来源，同一出口：

- :class:`SampleSource` —— 仓库自带的离线夹具（真实公开数据抽样，见
  ``examples/sample_data/README.md``），零网络、零密钥跑通全流程；
- :class:`CsvSource` —— 用户自己的投放后台导出（CSV / TSV，自动识别编码与分隔符）；
- :class:`CriteoSource` —— 可选：直读公开数据集的 parquet 分片，用于全量复算。

取数层不做任何清洗与口径判断——那是 :mod:`adops.clean` 的职责。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Protocol

import pandas as pd


class DataSourceError(RuntimeError):
    """取数失败（文件缺失、格式无法识别等）。"""


def read_table(path: str | Path, *, nrows: Optional[int] = None) -> pd.DataFrame:
    """读取表格文件，自动处理 gz / 编码 / 分隔符。

    Args:
        path: 文件路径（支持 ``.csv`` / ``.tsv`` / ``.txt``，可带 ``.gz``）。
        nrows: 只读前 N 行（大文件抽样用）。

    Returns:
        DataFrame（列名保持原样，尚未映射为规范字段）。
    """
    path = Path(path)
    if not path.exists():
        raise DataSourceError(f"数据文件不存在：{path}")

    suffixes = "".join(path.suffixes).lower()
    separator = "\t" if ".tsv" in suffixes else ","
    compression = "gzip" if suffixes.endswith(".gz") else None

    last_error: Optional[Exception] = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return pd.read_csv(
                path,
                sep=separator,
                compression=compression,
                encoding=encoding,
                nrows=nrows,
                low_memory=False,
            )
        except UnicodeDecodeError as exc:  # 换下一个编码再试
            last_error = exc
        except pd.errors.ParserError as exc:
            last_error = exc
            separator = None  # 让 pandas 自己嗅探分隔符
    raise DataSourceError(f"无法解析数据文件 {path}：{last_error}")


class AdDataSource(Protocol):
    """数据源协议：任何能返回一张原始表的对象都可以接进流水线。"""

    name: str

    def load(self) -> pd.DataFrame:  # pragma: no cover - 协议
        ...

    def meta(self) -> Dict[str, str]:  # pragma: no cover - 协议
        ...


class CsvSource:
    """读取用户自己的投放导出文件。"""

    def __init__(self, path: str | Path, *, label: str = "csv", nrows: Optional[int] = None):
        self.path = Path(path)
        self.name = label
        self._nrows = nrows

    def load(self) -> pd.DataFrame:
        return read_table(self.path, nrows=self._nrows)

    def meta(self) -> Dict[str, str]:
        return {"source": self.name, "path": str(self.path)}


class SampleSource(CsvSource):
    """读取仓库自带的离线夹具（默认数据源）。"""

    def __init__(self, path: str | Path, *, nrows: Optional[int] = None):
        super().__init__(path, label="sample", nrows=nrows)


class CriteoSource:
    """可选：直读公开数据集（Criteo Attribution）的本地副本。

    真实数据集体积很大（压缩后 600MB+），因此不进仓库；用
    ``scripts/fetch_public_data.py`` 下载后再指定路径即可全量复算。
    """

    name = "criteo"

    def __init__(self, path: str | Path, *, nrows: Optional[int] = None):
        self.path = Path(path)
        self._nrows = nrows

    def load(self) -> pd.DataFrame:
        frame = read_table(self.path, nrows=self._nrows)
        return frame

    def meta(self) -> Dict[str, str]:
        return {"source": self.name, "path": str(self.path)}
