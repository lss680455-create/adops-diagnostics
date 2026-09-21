# 示例与演示产物

## 目录

| 路径 | 内容 |
|---|---|
| `sample_data/` | 仓库自带的离线夹具（真实公开数据抽样，见其 `README.md`） |
| `sample_report/` | 用夹具跑出来的**成品报告**（`report.md`）、诊断发现 JSON 与 6 张图 —— 不装依赖也能先看交付物 |

## 两条演示命令

```bash
# 1) 曝光级日志（默认数据源，零网络零密钥）
python -m adops run --source sample --out outputs/demo

# 2) 聚合报表 + 中文列名（验证别名映射与「先加总再相除」口径）
python -m adops run --source csv \
  --data-file examples/sample_data/criteo_campaign_hourly_sample.csv \
  --out outputs/demo-aggregated
```

两次运行产出的报告结构完全相同 —— 输入的列名与布局不同，口径不变。

## 产出物清单（`outputs/<run>/`）

```
outputs/demo/
├── report.md                 # 主交付物：诊断报告
├── narrative.json            # 结论摘要（含 AI 守卫结果）
├── clean/ad_events.csv       # 清洗后的统一计数表（报告数字的第一层来源）
├── quality/checks.json       # 数据质量体检（逐项状态、阈值、实际值）
├── quality/checks.md
├── metrics/kpi.json          # 核心指标 + 漏斗 + 集中度
├── metrics/campaigns.csv     # 计划效率表
├── metrics/hourly.csv        # 分时效率
├── metrics/daily.csv         # 日趋势
├── metrics/allocation.json   # 预算再分配建议（含模型假设）
├── diagnose/findings.json    # 诊断发现（按优先级排序，含证据与建议）
├── experiment/plan.json      # 下一轮实验方案（样本量 / 时长 / 判定边界）
└── figures/*.png             # 六张标准图
```

报告里的每一个数字都能从这些中间产物逐层复算——这是「可复核」在工程上的实现方式，
不是文档里的一句承诺。
