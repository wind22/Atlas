# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Atlas is a **trend-based risk & opportunity monitor** for markets. The repo is currently at the **design stage**: there is no implementation code yet. `architecture.md` is the full, authoritative specification — treat §三 (signal scoring) and §四 (regime mapping) as an executable spec, not prose. The Python `.gitignore` confirms the intended language; nothing is built or tested yet.

When implementing, follow the module boundaries, parameters, and thresholds in `architecture.md` exactly rather than reinventing them.

## First principles that govern every design decision

`architecture.md` is founded on three inviolable rules (三条铁律). Any code or design choice that conflicts with them is wrong — change the design, not the principle:

1. **生存优先 / Survival first** — the system's primary job is the *brake* (risk score R), not the accelerator. Defense outranks offense: when `R ≥ 60`, the regime is Defensive regardless of how high the trend score T is.
2. **不预测,只响应 / Don't predict, respond** — only measure trends that have *already* happened. Never output buy/sell or price targets; output *exposure* language (hold / trim / de-risk) and probabilistic state.
3. **不对称下注 / Asymmetric bets** — cut losses short, let winners run. Risk alerts are more sensitive and higher-priority than opportunity alerts by design.

Corollary conventions: the system measures **market sentiment/danger, not intrinsic value**; parameters use **fixed industry-standard values** (200-day, 50-day, ADX 25, VIX 20) and are **never optimized to historical data** (anti-overfitting); every alert must be traceable to a concrete rule.

## Planned architecture

A daily pipeline over a 4-layer universe (大盘 market / 行业 sector / 多资产 multi-asset / 自选个股 stocks). Each ticker gets a **trend score T (0–100)** = Direction(40) + Momentum(30) + Strength(15) + Breadth(15), and an independently-computed **risk score R (0–100)**. T and R map to one of four regimes (🟢 Risk-On / 🟡 Caution / 🔴 Risk-Off / 🟠 Oversold). Output is a single self-contained HTML dashboard plus discrete alerts, pushed after US market close.

Module division (see `architecture.md` §7.3) — implement these as separable units:

| Module | Responsibility |
|---|---|
| `data_fetch` | Pull OHLCV per universe; handle gaps & adjustments |
| `indicators` | MAs, ADX, ATR, RS, MACD, drawdown, volatility |
| `scoring` | Five-dimension scoring → T and R per ticker (spec = §3) |
| `regime` | T/R → regime, with 2-day confirmation to avoid whipsaw (spec = §4) |
| `alerts` | Scan §5 rules for risk/opportunity triggers |
| `dashboard` | Render HTML |
| `runner` | Orchestrate pipeline + daily schedule + diff vs. yesterday's snapshot |

Key cross-cutting behaviors that require reading multiple sections to get right:
- **Regime confirmation**: a regime switch is only confirmed after the condition holds ~2 consecutive days; single-day flips are suppressed.
- **Layer precedence**: the market-layer regime is the master switch — when it is Defensive, individual stocks are de-risked regardless of their own high scores.
- **Incremental reporting**: the daily run compares against the prior day's snapshot and surfaces *changes* (regime switches, new alerts) at the top, not a full re-dump.
- **Snapshots**: persist raw data + daily results (CSV or SQLite) — required for the day-over-day diff and for backtesting.

## Planned tech stack

Python 3 · yfinance (data) · pandas / numpy / pandas-ta (indicators) · pure-Python rule engine (scoring/regime) · Jinja2 + self-contained HTML (output) · CSV or SQLite (snapshots) · scheduled task after US close (~16:30 ET).

## Validation philosophy

Backtesting validates **survival, not returns** (§9): does the system flip to Defensive and warn *before or early in* the big drawdowns (2008, 2020, 2018 Q4, 2022)? Evaluate lead/lag days, drawdown avoided, and whipsaw cost — not P&L. The system is expected to lag V-bottoms and whipsaw in ranges; that is an accepted premium, not a bug.

## Language

Domain content, comments, and docs are primarily in Chinese (中文). Keep canonical term names (趋势分 T, 风险分 R, 制度/Regime, 广度/Breadth) consistent with `architecture.md`.
