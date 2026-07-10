# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Atlas is a **trend-based risk & opportunity monitor** for markets, **implemented and running**. It is a **static-first data product**: a daily batch pipeline computes market state and publishes a stable JSON data contract under `public/data/`, which self-contained static pages (dashboard / detail / about) then consume. There is **no backend** — GitHub Actions runs the pipeline after US close and publishes to GitHub Pages.

`architecture.md` is the authoritative specification — treat §三 (signal scoring) and §四 (regime mapping) as an executable spec, not prose. When changing scoring/regime behavior, follow the module boundaries, parameters, and thresholds there exactly rather than reinventing them.

## First principles that govern every design decision

`architecture.md` is founded on three inviolable rules (三条铁律). Any code or design choice that conflicts with them is wrong — change the design, not the principle:

1. **生存优先 / Survival first** — the system's primary job is the *brake* (risk score R), not the accelerator. Defense outranks offense: when `R ≥ 60`, the regime is Defensive regardless of how high the trend score T is.
2. **不预测,只响应 / Don't predict, respond** — only measure trends that have *already* happened. Never output buy/sell or price targets; output *exposure* language (hold / trim / de-risk) and probabilistic state. **The data contract forbids any forward-looking field** (future returns, forecasts, price targets) — this is enforced by tests (`test_artifacts.py`, `test_similarity.py`).
3. **不对称下注 / Asymmetric bets** — cut losses short, let winners run. Risk alerts are more sensitive and higher-priority than opportunity alerts by design.

Corollary conventions: the system measures **market sentiment/danger, not intrinsic value**; parameters use **fixed industry-standard values** (200-day, 50-day, ADX 25, VIX 20) and are **never optimized to historical data** (anti-overfitting); every alert must be traceable to a concrete rule.

## Architecture

A daily pipeline over a 4-layer universe (大盘 market / 行业 sector / 多资产 multi-asset / 自选个股 stocks). Each ticker gets a **trend score T (0–100)** = Direction(40) + Momentum(30) + Strength(15) + Breadth(15), and an independently-computed **risk score R (0–100)**. T and R map to one of four regimes (🟢 Risk-On / 🟡 Caution / 🔴 Risk-Off / 🟠 Oversold). The pipeline publishes a JSON data contract plus a self-contained HTML dashboard, after US market close.

Data flows through layered packages (each independently testable):

```
data_fetch ─→ indicators ─→ scoring ─→ regime + alerts ─→ DailyReport
                                                              │
        report/ (explain · state_machine · similarity) ──────┤
                                                              ▼
   storage/ (snapshot_store 持久化 + artifacts 数据产物) ── public/data/*.json
                                                              │
                          site/ (dashboard · detail · about) ─┘  ← 消费视图模型
```

Module map:

| Path | Responsibility |
|---|---|
| `atlas/config.py` | Universe, parameters, thresholds, weights (fixed, never tuned to history) |
| `atlas/types.py` | Shared data contracts (`DailyReport`, `TickerResult`, …); single source of shapes |
| `atlas/data_fetch.py` | Pull OHLCV per universe (yfinance) / deterministic synthetic data; gaps & 除权 |
| `atlas/indicators.py` | MAs, ADX, MACD, RSI, relative strength, drawdown, volatility, breadth |
| `atlas/scoring.py` | Five-dimension scoring → T and R per ticker (spec = §3) |
| `atlas/regime.py` | T/R → regime with 2-day confirmation to avoid whipsaw (spec = §4) |
| `atlas/alerts.py` | Scan §5 rules for risk/opportunity triggers |
| `atlas/backtest.py` | Survival backtest (§9): regime-timed exposure vs buy-and-hold drawdown |
| `atlas/report/` | Human-facing report layer (pure-derived): `explain` (今日结论/风险/机会/变化), `state_machine` (制度持续天数/上次切换), `similarity` (历史相似状态 — descriptive only) |
| `atlas/storage/` | `snapshot_store` (SQLite persistence), `artifacts` (publish `public/data/*.json` contract) |
| `atlas/site/` | Static renderers: `dashboard` (self-contained HTML + view model), `detail` (per-stock pages), `about` (algorithm page) |
| `atlas/pipelines/daily.py` | Orchestrates one trading day end-to-end |
| `atlas/runner.py` | argparse CLI (`python -m atlas`); delegates to `pipelines.daily.run` |

**Compat shims:** `atlas/snapshot.py`, `atlas/dashboard.py`, `atlas/detail.py`, `atlas/about.py` are thin re-export shims kept for backward-compatible imports — the real code lives in `storage/` and `site/`. New code should import from the canonical locations.

## Data contract (`public/data/`)

The published JSON is the product; HTML consumes it. `schema.json` is the machine-readable, versioned source of truth (`SCHEMA_VERSION` only ever increments; fields are only added, never removed or retyped).

| File | Content |
|---|---|
| `latest.json` / `daily/{date}.json` | Today's report envelope: `meta` + `report` (`DailyReport.to_dict()`) + `explain` + `state` + `similar` |
| `regime_history.json` | Lightweight regime time series (accumulates across runs) |
| `dashboard_view.json` | The dashboard **view model** — HTML and this JSON are the same source (re-rendering from it reproduces `index.html` byte-for-byte) |
| `universe.json` | The four-layer ticker → name map |
| `manifest.json` | Available dates + latest pointer + file index |
| `schema.json` | Contract self-description + `schema_version` |

## State model & CI

- **State lives on a machine-owned `data` branch, not `main`.** `deploy-pages.yml` restores yesterday's state (SQLite + published `public/data/`) at build start and pushes today's state back to `data` — it is the branch's only writer, so there is no push race. `main` holds only source + a frozen seed snapshot.
- **Regime confirmation:** a regime switch is only confirmed after the condition holds ~2 consecutive days; single-day flips are suppressed. This needs yesterday's snapshot, which is why state continuity matters.
- **Layer precedence:** the market-layer regime is the master switch — when it is Defensive, individual stocks are de-risked regardless of their own high scores.
- **Incremental reporting:** the daily run compares against the prior day's snapshot and surfaces *changes* (regime switches, new alerts) at the top, not a full re-dump. See `report/explain.py` `delta_from_yesterday`.

## Dev workflow

```bash
pip install -r requirements.txt
python -m atlas --offline --output public/index.html   # deterministic offline run (no network)
python -m pytest                                         # full black-box suite
```

Every change must keep the suite green. Tests are black-box (build public contracts from `atlas.types`, fixed seeds, fixed dates — never wall-clock). The 铁律 are encoded as tests: no forward-looking fields in any JSON product; Risk-Off headline stays defensive regardless of opportunity alerts.

## Tech stack

Python 3 · yfinance (data) · pandas / numpy (indicators) · pure-Python rule engine (scoring/regime) · Jinja2 + self-contained HTML (output) · SQLite snapshots + JSON data contract (state) · GitHub Actions after US close (~16:30 ET) → GitHub Pages.

## Validation philosophy

Backtesting validates **survival, not returns** (§9): does the system flip to Defensive and warn *before or early in* the big drawdowns (2008, 2020, 2018 Q4, 2022)? Evaluate lead/lag days, drawdown avoided, and whipsaw cost — not P&L. The system is expected to lag V-bottoms and whipsaw in ranges; that is an accepted premium, not a bug.

## Language

Domain content, comments, and docs are primarily in Chinese (中文). Keep canonical term names (趋势分 T, 风险分 R, 制度/Regime, 广度/Breadth) consistent with `architecture.md`.
