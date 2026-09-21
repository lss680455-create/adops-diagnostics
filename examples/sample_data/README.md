# 离线夹具说明（examples/sample_data）

这里的两份夹具都**来自真实公开投放数据**，不是编造的演示数据。

## 数据来源

| 项 | 内容 |
|---|---|
| 数据集 | Criteo Attribution Modeling for Bidding Dataset（Criteo Research 公开研究数据集） |
| 论文 | Diemert & Meynet et al., *Attribution Modeling Increases Efficiency of Bidding in Display Advertising*, AdKDD/TargetAd @ KDD 2017 |
| 许可 | CC BY-NC-SA 4.0（**非商用**；商用请另行确认） |
| 规模 | 16,468,027 次真实曝光，45K 转化，约 700 个广告计划，30 天 |
| 下载 | `python scripts/fetch_public_data.py --out data/criteo_attribution.tsv.gz` |
| 生成 | `python scripts/build_fixture.py --input data/criteo_attribution.tsv.gz` |

原始文件的 sha256 记在 `fixture_meta.json` 里，夹具可复现。

## 两份夹具

| 文件 | 内容 | 对应链路的演示 |
|---|---|---|
| `criteo_impression_sample.csv.gz` | 按「每 55 行取 1 行」抽出的 **299,418 条曝光级日志**，保留 click / conversion / attribution / cost 标记 | 曝光级日志 → 清洗 → 质量体检 → 指标（默认数据源） |
| `criteo_campaign_hourly_sample.csv` | 同一份数据聚合出的**「计划 × 天 × 小时」中文报表**（Top 50 计划，36,982 行） | 聚合报表 + 中文列名别名映射 → 同一套指标口径 |

两份夹具的指标口径完全一致（见 `docs/METRICS.md`），差异只在输入的布局与列名——
这正是「同一个口径能吃下不同后台导出」的验证。

## 必须知道的数据边界（报告里也会自动印出）

1. **绝对水平不可当基准**。该数据集经过抽样与脱敏，CTR / CPM / CPA 的绝对数值
   不代表任何行业真实水平。本仓库用它验证的是**口径、流程与诊断逻辑**，
   不能用来横向对比外部账户。
2. **消耗不是货币**。源数据的 `cost` 字段是数据集作者明确说明的「变换后的价格」，
   因此报告里的金额一律标为「口径单位」，比较只能用相对关系（倍数、占比）。
3. **时间戳是相对秒数**。源数据的时间戳是「距首条曝光的秒数」，不是日历时间。
   脚本据此还原为「第 N 天 + 小时」（`D001` 形式），**不伪造具体日期**；
   报告的时间口径会标注为 `relative_day`。
4. **换成自己的数据**：`python -m adops run --source csv --data-file <你的导出>`。
   口径与阈值不变，结论立刻可用于决策。

## 为什么不用合成的假数据

合成数据可以随便调好看（想让 CTR 是多少就是多少），但它无法暴露真实数据的坑：
离散长尾的消耗分布、样本量差异极大的计划、归因覆盖不全、相对时间戳。
这个项目里有两处规则就是被真实数据逼出来的：

- 消耗异常检测从「按明细行」改成「按日汇总」——因为逐条检测在真实数据上误报 7%；
- 去重必须包含 `user_id`——否则会产生 2.76% 的假重复率，把数据误判成「不可用」。

这两条都写进了代码注释与测试（`tests/test_quality.py`、`tests/test_normalize.py`），
可以在面试里直接讲。
