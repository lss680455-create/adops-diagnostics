<div align="center">

<img src="docs/assets/hero.svg" width="100%" alt="adops-diagnostics">

[![CI](https://github.com/lss680455-create/adops-diagnostics/actions/workflows/ci.yml/badge.svg)](https://github.com/lss680455-create/adops-diagnostics/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-191%20offline-success)
![API keys](https://img.shields.io/badge/API%20keys-none%20required-brightgreen)

[中文](README.md) · [Features](#features) · [Quickstart](#quickstart) · [Why](#design-trade-offs) · [Docs](#documentation)

</div>

---

## What it is

**adops-diagnostics** turns ad-campaign review into a pipeline: give it an ad-platform
export (or the bundled offline fixture) and it returns a **diagnostic report you can send
to a stakeholder** — which campaigns are dragging, which funnel layer is broken, where the
budget should move, and how to design the next experiment. Every conclusion carries
evidence, an action, and a blast radius.

Three disciplines:

- **Recomputable** — every number in the report can be traced back through the staged
  artifacts under `outputs/`.
- **Deterministic** — two runs on the same input produce byte-identical reports
  (except the generation timestamp), so reports can be diffed.
- **Zero keys** — no API key and no network are required to run the whole pipeline.

The design trade-off in one line: **rules make the decisions, AI writes them up.**

## Features

- **9-stage pipeline** — collect → clean → quality → metrics → diagnose → charts →
  allocation → experiment → report. Each stage is unit-testable and dumps artifacts.
- **Two input layouts, one metric vocabulary** — impression-level logs and aggregated
  reports (including Chinese column names) converge on the same canonical table.
- **12 data-quality checks** — completeness, duplication, logical consistency, cost
  outliers, time coverage, attribution coverage; every sanitisation is logged, never silent.
- **12 deterministic diagnostic rules** — all gated on minimum sample size; rate
  comparisons pass a significance test; hourly opportunities get Bonferroni correction.
- **Dependency-free statistics** — two-proportion z-test, Wilson intervals, sample size /
  MDE / power inversion, O'Brien-Fleming sequential boundaries, standard library only.
- **Budget reallocation simulator** — deterministic, outputs "which direction to try
  first", and prints the first-order assumption together with the result.
- **AI narration behind a number-binding guard** — conclusions come from rules; the model
  only writes them up, and every number it emits must be traceable to the evidence table
  (5% tolerance for rounding) or the whole passage is discarded for a deterministic template.
- **Review SQL** — six queries whose definitions match the report's metrics exactly, so an
  analyst can re-verify the conclusions in the warehouse.
- **6 standard charts** — spend vs CPA, CTR×CVR quadrants, funnel, hourly dual-axis,
  Lorenz curve, daily trend (auto-degrades to English labels without CJK fonts).

## Quickstart

```bash
git clone https://github.com/lss680455-create/adops-diagnostics.git
cd adops-diagnostics
pip install -e ".[charts,dev]"

# Bundled offline fixture (real public ad data; no network, no keys)
python -m adops run --source sample --out outputs/demo
```

Open `outputs/demo/report.md` and `outputs/demo/figures/`.

With your own data (CSV/TSV export from any ad platform; column names auto-detected):

```bash
python -m adops run --source csv --data-file my_export.csv --out outputs/week
```

Single-purpose commands:

```bash
python -m adops quality  --source sample                                  # data-quality check only
python -m adops diagnose --source sample                                  # findings only
python -m adops design   --baseline 0.01 --daily 50000 --mde-rel 0.1      # sample size / MDE
python -m adops evaluate --control 100 10000 --variant 150 10000          # read out an experiment
python -m adops billing  --ctr 0.02 --cpc 0.5 --cvr 0.05 --target-cpa 10  # CPM/CPC/CPA/oCPC math
python -m adops sql --name 计划效率                                        # review SQL
```

## What the report looks like

Nine fixed sections, each answering a question that really gets asked in a review:
summary · KPIs & billing identity check · data-quality health · campaign efficiency with
quantified verdicts · hourly insight · findings (evidence → action → blast radius) ·
budget reallocation · next experiment design · metric definitions & review SQL.

Sample finding:

```markdown
### R02　[高] 归因覆盖率不足，CPA 被系统性高估
- **对象**：归因回传链路
- **影响消耗占比**：100.00%　**优先级分**：3.0000
- **证据**：
  - 归因转化 8,099 / 全部转化 14,823 = 54.64%
  - 门店阈值 80.00%，当前低于阈值 25.36%
- **建议动作**：核对转化回传埋点、归因窗口与去重规则……
```

## Data

The bundled fixtures come from the **Criteo Attribution Modeling for Bidding Dataset**
(public research data, 16,468,027 real impressions, CC BY-NC-SA 4.0) and are regenerated
reproducibly by [`scripts/build_fixture.py`](scripts/build_fixture.py).

> **Boundaries**: this dataset is sub-sampled and anonymised, so absolute CTR / CPM / CPA
> levels are **not industry benchmarks**; its `cost` field is a transformed price, not
> currency; timestamps are relative seconds, honestly labelled `relative_day` in the report.
> The fixtures validate metric definitions, pipeline behaviour and diagnostic logic.
> Point the pipeline at your own export and the conclusions become decision-ready.
>
> Every report prints these boundaries automatically — a correct number that gets
> misread is still a failure.

## Design trade-offs

**Why rules diagnose instead of the model.** Rules are recomputable (same input, same
output), refutable (a user can point at the exact sample-size gate that skipped their
campaign), and regression-testable (change a threshold and see which conclusions move).
So AI improves readability and delivery speed — it does not decide which campaign to cut.

**Why the AI output needs a number-binding guard.** The dangerous failure is not bad prose
but an invented number: someone cuts budget based on it and nobody can reproduce where it
came from. So the discipline is enforced in code — every number must match the evidence
table, otherwise the passage is discarded and the deterministic template is used.
This is a failing CI check, not a promise in a doc.

**Why every threshold requires adequate sample size.** A missed opportunity costs far less
than losing trust in the report: campaigns below 10 conversions get no efficiency verdict,
below 1,000 impressions no CTR verdict, HHI is skipped below 5 campaigns, and hourly
opportunities are Bonferroni-corrected. The "what it deliberately does not report" list is
as important as the rule table.

**Why no scipy.** Only a two-proportion test and normal quantiles are needed; the standard
library's `statistics.NormalDist` and `math.erf` suffice, formulas stay readable in code,
and numeric conventions cannot drift with library versions.

## Tests

```bash
python -m pytest -q     # 191 tests, fully offline, no keys, no files outside the repo
```

Coverage highlights: metric discipline (`sum-then-divide` vs naive mean-of-ratios, 17× off
on constructed data), statistics against known values, rules that must fire *and* rules that
must stay silent, the AI guard's fallback paths, and pipeline determinism. CI runs on Python
3.9 / 3.11 / 3.12 and additionally asserts that the narrative contains no unbound numbers and
that the report keeps its data-boundary notice.

## Documentation

| Doc | Content |
|---|---|
| [`docs/PRODUCT.md`](docs/PRODUCT.md) | Product framing: users, the nine questions, design rationale, acceptance criteria, iteration log, explicit non-goals |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Nine stages, data contract, the two key cleaning decisions, AI layer constraints, dependency choices |
| [`docs/METRICS.md`](docs/METRICS.md) | Metric definitions, billing identities, structural metrics, statistical conventions |
| [`docs/DIAGNOSIS-RULES.md`](docs/DIAGNOSIS-RULES.md) | All 12 rules with thresholds, rationale and the "does not report" list |
| [`CHANGELOG.md`](CHANGELOG.md) | Version history, including two designs rewritten after real data contradicted them |

## License

Code is MIT. The bundled fixtures come from the public Criteo dataset
(**CC BY-NC-SA 4.0, non-commercial**), copyright of their original authors — see
[`examples/sample_data/README.md`](examples/sample_data/README.md).
