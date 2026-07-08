"""Basic regime backtest — validates SURVIVAL, not returns (architecture.md §9).

铁律 Ⅰ（生存优先）的直接检验：回放历史数据，在每根K线上重算 T/R 评分与制度，
应用 N 日确认后得到一条「随制度调整仓位」的资金曲线，再对比它与买入持有的最大回撤。
我们要看的不是赚了多少，而是系统能否在大跌中把敞口降下来、少受重伤。

Best-effort and dependency-light: imports cleanly and runs fully offline.
"""
from __future__ import annotations

import argparse

import pandas as pd

from . import config, data_fetch, indicators, regime, scoring
from .config import Layer
from .types import Regime


# Exposure held under each confirmed regime (applied next-bar, no lookahead).
_EXPOSURE: dict[Regime, float] = {
    Regime.RISK_ON: 1.0,
    Regime.CAUTION: 0.5,
    Regime.OVERSOLD: 0.5,
    Regime.RISK_OFF: 0.0,
}


def _max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough drawdown of an equity curve, as a magnitude."""
    if equity is None or len(equity) == 0:
        return 0.0
    running_peak = equity.cummax()
    drawdown = (running_peak - equity) / running_peak
    return float(drawdown.max())


def _confirm_series(raw: list[Regime]) -> list[Regime]:
    """Apply the N-day confirmation gate to a raw-regime stream (chronological).

    A switch to X confirms only after config.REGIME_CONFIRM_DAYS consecutive
    equal raw regimes; otherwise the previously confirmed regime is held.
    """
    n = config.REGIME_CONFIRM_DAYS
    confirmed: list[Regime] = []
    current: Regime | None = None
    for i, today in enumerate(raw):
        if current is None:
            current = today
        elif today != current:
            window = raw[i - n + 1 : i + 1]
            if len(window) >= n and all(r == today for r in window):
                current = today
        confirmed.append(current)
    return confirmed


def backtest_regime(
    ticker: str = "SPY", *, offline: bool = True, period: str = "10y"
) -> pd.DataFrame:
    """Walk history bar-by-bar, scoring the regime, and gate exposure by it.

    Returns a DataFrame indexed by date with columns:
        close, T, R, raw_regime, regime, exposure,
        bh_equity, gated_equity
    plus DataFrame ``.attrs`` carrying the summary metrics.
    """
    tickers = [ticker, config.BENCHMARK]
    if offline:
        frames = data_fetch.synthetic_prices(tickers)
    else:
        frames = data_fetch.fetch_prices(tickers, period=period)

    df = frames.get(ticker)
    if df is None or len(df) == 0:
        raise ValueError(f"{ticker}: 无法获取价格数据")
    bench = frames.get(config.BENCHMARK, df)

    name = config.name_of(ticker)
    layer = config.layer_of(ticker)
    start = config.MA_LONG + config.MA_SLOPE_LOOKBACK

    dates: list = []
    rows: list[dict] = []
    for i in range(start, len(df)):
        window = df.iloc[: i + 1]
        bench_window = bench.iloc[: i + 1]
        try:
            ind = indicators.compute_indicators(
                window, bench_window, ticker=ticker, name=name, layer=layer
            )
        except ValueError:
            continue
        result = scoring.score_ticker(ind, breadth_pct=0.5, vix=None)
        raw, _reason = regime.classify(result)
        dates.append(window.index[-1])
        rows.append(
            {
                "close": float(window["Close"].iloc[-1]),
                "T": result.T,
                "R": result.R,
                "raw_regime": raw.value,
            }
        )

    out = pd.DataFrame(rows, index=pd.DatetimeIndex(dates))
    if out.empty:
        return out

    raw_regimes = [Regime(v) for v in out["raw_regime"]]
    confirmed = _confirm_series(raw_regimes)
    out["regime"] = [r.value for r in confirmed]
    out["exposure"] = [_EXPOSURE[r] for r in confirmed]

    # Next-bar exposure to avoid lookahead: today's regime governs tomorrow.
    bh_ret = out["close"].pct_change().fillna(0.0)
    gated_ret = out["exposure"].shift(1).fillna(0.0) * bh_ret
    out["bh_equity"] = (1.0 + bh_ret).cumprod()
    out["gated_equity"] = (1.0 + gated_ret).cumprod()

    bh_dd = _max_drawdown(out["bh_equity"])
    gated_dd = _max_drawdown(out["gated_equity"])

    # Count defensive switches: confirmed regime turning to RISK_OFF.
    defensive_switches = 0
    for prev, cur in zip(confirmed[:-1], confirmed[1:]):
        if cur == Regime.RISK_OFF and prev != Regime.RISK_OFF:
            defensive_switches += 1

    out.attrs["ticker"] = ticker
    out.attrs["bh_maxdd"] = bh_dd
    out.attrs["gated_maxdd"] = gated_dd
    out.attrs["defensive_switches"] = defensive_switches
    out.attrs["risk_off_stretches"] = _risk_off_stretches(confirmed, out.index)
    return out


def _risk_off_stretches(confirmed: list[Regime], index: pd.DatetimeIndex):
    """Contiguous RISK_OFF spans as (start_date, end_date, length), longest first."""
    stretches: list[tuple[str, str, int]] = []
    i = 0
    n = len(confirmed)
    while i < n:
        if confirmed[i] == Regime.RISK_OFF:
            j = i
            while j + 1 < n and confirmed[j + 1] == Regime.RISK_OFF:
                j += 1
            stretches.append(
                (
                    str(index[i].date()),
                    str(index[j].date()),
                    j - i + 1,
                )
            )
            i = j + 1
        else:
            i += 1
    stretches.sort(key=lambda s: s[2], reverse=True)
    return stretches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Atlas 制度回测 — 验证生存（避开大跌），非收益。"
    )
    parser.add_argument("--ticker", default="SPY", help="回测标的（默认 SPY）")
    parser.add_argument(
        "--online", action="store_true", help="使用 yfinance 实时数据（默认离线合成）"
    )
    parser.add_argument("--period", default="10y", help="在线数据历史窗口（默认 10y）")
    args = parser.parse_args(argv)

    out = backtest_regime(
        ticker=args.ticker, offline=not args.online, period=args.period
    )

    print(f"=== Atlas 制度回测：{args.ticker} ===")
    if out.empty:
        print("数据不足，无法回测。")
        return 0

    bh_dd = out.attrs["bh_maxdd"]
    gated_dd = out.attrs["gated_maxdd"]
    print(f"样本区间：{out.index[0].date()} → {out.index[-1].date()}（{len(out)} 根K线）")
    print(f"买入持有 最大回撤：{bh_dd * 100:.1f}%")
    print(f"制度调仓 最大回撤：{gated_dd * 100:.1f}%")
    saved = (bh_dd - gated_dd) * 100
    print(f"回撤改善：{saved:+.1f} 个百分点（越大越说明避开了深跌）")
    print(f"转防御次数（切到 🔴 Risk-Off）：{out.attrs['defensive_switches']}")

    stretches = out.attrs["risk_off_stretches"]
    if stretches:
        print("最长的 🔴 防御区区间：")
        for start, end, length in stretches[:5]:
            print(f"  {start} → {end}（{length} 个交易日）")
    else:
        print("样本内未出现确认的防御区。")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
