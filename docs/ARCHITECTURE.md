# 架构（ARCHITECTURE）

## 设计原则

1. **口径只能有一处定义**。比率的算法写在 `adops/metrics/ads.py` 的纯函数里，
   报告、SQL、图表、测试全部从同一处取；发现两处口径不一致就算 bug。
2. **先加总再相除**。聚合层只做加总，比率全部在聚合之后算。
3. **每个阶段可单测，中间产物全部落盘**。报告里的每个数字都能顺着
   `clean/ → metrics/ → diagnose/` 往回查到源头。
4. **结论确定性、表达可 AI**。诊断由规则产出（同输入同输出），AI 只负责把结论
   写成通顺的话，并且必须过数字回指校验。
5. **零密钥、零网络也能跑全流程**。没有 API key、没有外网，报告照常产出。

## 九阶段

| # | 阶段 | 模块 | 输入 → 输出 |
|---|---|---|---|
| 1 | 取数 | `collect/` | 文件/夹具 → 原始表（`raw_frame`） |
| 2 | 清洗 | `clean/normalize.py` | 原始表 → 统一计数表 + 审计报告（`NormalizeReport`） |
| 3 | 体检 | `clean/quality.py` | 计数表 → 12 项检查（`ok/warn/fail` + 阈值 + 实际值） |
| 4 | 指标 | `metrics/` | 计数表 → KPI / 计划表 / 分时 / 日趋势 / 集中度 |
| 5 | 诊断 | `diagnose/` | 上面全部 → 发现清单（规则 + 证据 + 建议 + 优先级） |
| 6 | 图表 | `charts/` | 指标与聚合表 → 6 张标准图 |
| 7 | 调仓 | `metrics/allocation.py` | 计划表 → 再分配建议（含模型假设） |
| 8 | 实验 | `experiment/` | 基准率 + 日流量 → 样本量 / 时长 / 判定边界 |
| 9 | 成稿 | `ai/` + `compose/` | 全部 → 结论摘要 + 完整 Markdown 报告 |

阶段之间通过 `PipelineContext.data` 传递，**不共享可变状态**：任一阶段只读上游键、
写自己的键。这样单跑一个阶段（`python -m adops quality`）也能工作。

## 数据契约

清洗层的输出是一张固定形态的表，下游只认它：

```
date | hour | campaign_id | channel | placement | creative
     | impressions | clicks | conversions | cost | attributed_conversions
     | user_id | click_nb | time_since_last_click   （后三列可选）
```

- 计数器为 `float64`，缺失补 0；`attributed_conversions` 缺失记 `NaN`（不是 0）——
  「没有归因字段」和「归因转化是 0」是两件事。
- 维度列缺失时该维度不参与拆分，**不报错**（真实导出常常缺列）。
- 无法识别的来源列不进表，列名记进审计报告。

## 清洗层的两处关键判定

### 1. 布局识别（曝光级 vs 聚合报表）

两者出口同构，但口径不同：

- 曝光级：每行一次曝光，`impressions = 1`，`click` 是 0/1 标记；
- 聚合报表：每行已含曝光量/点击量，直接取列值。

自动判定在「同时有 0/1 的 click 列和 impressions 计数列」时会误判，因此加了一条数据
校验：click 全为 0/1 且 impressions 恒等于 1 时仍按曝光级处理。
必要时可用 `--layout impression|aggregated` 显式声明（`configs/default.yaml` 亦有默认值）。

### 2. 时间口径

| 输入 | 处理 | 报告标注 |
|---|---|---|
| 可解析的日期字符串 | 转 `YYYY-MM-DD` | `absolute` |
| 秒级绝对时间戳（≥ 1e9） | 转日历日期（UTC） | `absolute` |
| 相对秒数（公开数据集常见） | 还原为「第 N 天 + 小时」（`D001`） | `relative_day` |
| 无时间列 | 不阻塞，趋势类规则自动跳过 | `missing` |

**不伪造日历日期**：宁可把口径如实标成 `relative_day`，也不假装它是真实日期。

## 诊断层

```
DiagnosisInput（上游产出的快照）
        ↓  12 条规则，逐条独立
Finding（rule_id / level / 证据 / 建议 / 影响占比）
        ↓  scoring.rank_findings（确定性排序）
报告第 5 节 + narrative
```

- 规则是**纯函数式**的：`f(ctx) -> Finding | None`，便于单测与替换。
- 单条规则抛异常不会拖垮整份报告，会降级成一条「规则执行异常」发现（有测试覆盖）。
- 比率类问题一律「先过样本量门槛，再做显著性检验」，两道都不达标就不报。

## AI 层（`adops/ai/`）

```
规则产出的 Finding（确定性）
        ↓  build_narrative_payload —— 组装「证据表」，只放已算好的数字
        ↓  模板叙述（默认，零密钥）        或        LLM 叙述（可选）
        ↓  guardrail.verify_numbers —— 逐个数校验能否回指证据表
        ↓  通过 → 采用模型文本；不通过 → 回落到模板文本
报告第 0 节 + narrative.json
```

三条硬约束：

1. **模型不能改结论**。诊断结论来自规则，模型只负责表达。
2. **模型不能引入数字**。输出里每个数字都必须在证据表中按容差（默认 5%，容忍四舍五入）
   匹配到，否则整段作废。单/双位整数默认不校验（「前 3 名」这类结构性数字）。
3. **失败必须降级**。网络失败、配额耗尽、无密钥、输出为空——一律回落到模板，
   报告永远能出。

CI 里有一条专门的守卫步骤（`scripts/check_narrative.py`）：断言结论中不存在不可回指的
数字。承诺写成测试，才不会被某次改动悄悄绕过。

## 目录结构

```
adops/
├── config.py            # 集中配置（YAML + 环境变量），阈值只在这里
├── pipeline.py          # 九阶段装配 + PipelineContext
├── cli.py               # run / quality / diagnose / sql / design / evaluate / billing
├── collect/             # schema（列名别名）+ impressions（数据源）
├── clean/               # normalize（布局/时间/去重/钳位）+ quality（12 项体检）
├── metrics/             # ads（纯函数）+ aggregate（先加总再相除）+ allocation（调仓）
├── diagnose/            # rules（12 条规则）+ scoring（优先级）
├── experiment/          # tests_stats（零依赖统计）+ design（实验方案）
├── charts/              # report_charts（6 张图，无中文字体自动降级为英文）
├── compose/             # report（报告）+ sql（复核用 SQL）
├── ai/                  # narrator（叙述）+ guardrail（数字回指）+ prompts
└── llm/                 # client（OpenAI 兼容，仅标准库）
```

## 依赖取舍

| 依赖 | 用在哪 | 为什么不用别的 |
|---|---|---|
| pandas / numpy | 数据读取与聚合 | 投放数据本质是表格运算 |
| PyYAML | 配置 | 阈值集中、可读 |
| matplotlib（可选） | 图表 | 无头环境可 `--no-charts` 跳过 |
| **不引入 scipy** | 统计 | 只需要两比例检验与正态分位数，标准库 `statistics.NormalDist` + `math.erf` 足够；自带实现能把公式写在代码里，结论可逐行复核 |
| **不引入请求库** | 调 LLM | `urllib.request` 足够，少一个依赖 |

## 已知边界

- 归因建模（多触点归因、Shapley 值）没有实现：本仓库只把「归因覆盖率」当作数据健康
  指标，不做归因本身。
- 边际效率曲线没有建模：调仓建议用「按当前 CPA 线性缩放」的一阶近似，假设随结果一起
  打印，落地需分批执行并观察。
- 只支持批式跑批，不做实时出价/在线学习。
