"""Price data acquisition (架构 §2 数据层).

Two ways in:
  * fetch_prices / fetch_vix  — live pull via yfinance (auto-adjusted OHLCV).
  * synthetic_prices          — deterministic offline generator so the whole
    pipeline runs with no network and reproducible results (workflow runtime
    forbids wall-clock, so the synthetic calendar ends on a FIXED date).

Every DataFrame returned here has an ascending DatetimeIndex and the columns
Open, High, Low, Close, Volume (closes already adjusted).
"""
from __future__ import annotations

import hashlib
import sys

import numpy as np
import pandas as pd

from . import config

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]

# Fixed anchor for the synthetic calendar. Do NOT use datetime.now/today — the
# workflow runtime forbids wall-clock so runs stay reproducible.
_SYNTH_END = "2026-07-08"


def _warn(msg: str) -> None:
    print(f"[data_fetch] {msg}", file=sys.stderr)


def _stable_hash(text: str) -> int:
    """Deterministic non-negative int from a string (md5, cross-run stable).

    Python's built-in hash() is salted per-process, so it must not be used
    where reproducibility matters.
    """
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _normalize(df: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """Coerce a raw yfinance frame into ascending OHLCV; None if unusable."""
    if df is None or len(df) == 0:
        return None

    # yfinance may return MultiIndex columns (field, ticker) — flatten to field.
    if isinstance(df.columns, pd.MultiIndex):
        # Prefer selecting this ticker's slice, else drop the ticker level.
        levels = df.columns
        if ticker in levels.get_level_values(-1):
            df = df.xs(ticker, axis=1, level=-1)
        else:
            df = df.copy()
            df.columns = levels.get_level_values(0)

    # Some columns may be missing (e.g. no Volume for indices); keep what exists.
    have = [c for c in _OHLCV if c in df.columns]
    if "Close" not in have:
        return None
    df = df[have].copy()

    # Fill any missing OHLCV columns from Close so the shape is uniform.
    for col in _OHLCV:
        if col not in df.columns:
            df[col] = 0.0 if col == "Volume" else df["Close"]
    df = df[_OHLCV]

    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()
    df = df.dropna(subset=["Close"])
    if len(df) == 0:
        return None
    return df


def fetch_prices(
    tickers: list[str], period: str = config.DEFAULT_PERIOD
) -> dict[str, pd.DataFrame]:
    """Download adjusted OHLCV for each ticker; drop failures with a warning."""
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        try:
            raw = yf.download(
                ticker,
                period=period,
                auto_adjust=True,
                progress=False,
                threads=False,
            )
        except Exception as exc:  # noqa: BLE001 — resilience over precision
            _warn(f"{ticker}: download failed ({exc!r}); skipping")
            continue
        norm = _normalize(raw, ticker)
        if norm is None:
            _warn(f"{ticker}: empty / unusable data; skipping")
            continue
        out[ticker] = norm
    return out


def fetch_vix(period: str = config.DEFAULT_PERIOD) -> pd.DataFrame | None:
    """Fetch the VIX (config.VIX_TICKER); None on any failure."""
    import yfinance as yf

    try:
        raw = yf.download(
            config.VIX_TICKER,
            period=period,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception as exc:  # noqa: BLE001
        _warn(f"VIX: download failed ({exc!r})")
        return None
    return _normalize(raw, config.VIX_TICKER)


# --------------------------------------------------------------------------
# Deterministic offline generator.
# --------------------------------------------------------------------------
def _business_index(days: int, end: str) -> pd.DatetimeIndex:
    """`days` business days ending on (and including) `end`."""
    return pd.bdate_range(end=pd.Timestamp(end), periods=days)


def _synth_equity(
    ticker: str, index: pd.DatetimeIndex, seed: int
) -> tuple[pd.DataFrame, np.ndarray]:
    """Geometric random walk for one ticker. Returns (frame, close array)."""
    rng = np.random.default_rng(seed + _stable_hash(ticker))
    n = len(index)

    # Per-ticker regime knobs from the hash → mix of up / down / choppy.
    drift = rng.uniform(-0.25, 0.35)   # annual drift
    vol = rng.uniform(0.12, 0.45)      # annual volatility
    dt = 1.0 / config.TRADING_DAYS_YEAR

    mu = (drift - 0.5 * vol * vol) * dt
    sigma = vol * np.sqrt(dt)
    shocks = rng.normal(mu, sigma, size=n)
    start = float(rng.uniform(40.0, 400.0))
    close = start * np.exp(np.cumsum(shocks))

    # OHLC derived around Close with a small intrabar range.
    daily_range = np.abs(rng.normal(0.0, sigma, size=n)) + 0.002
    prev = np.concatenate([[close[0]], close[:-1]])
    open_ = prev * (1.0 + rng.normal(0.0, sigma * 0.5, size=n))
    hi_lo_span = close * daily_range
    high = np.maximum(open_, close) + hi_lo_span * rng.uniform(0.2, 1.0, size=n)
    low = np.minimum(open_, close) - hi_lo_span * rng.uniform(0.2, 1.0, size=n)
    low = np.clip(low, 0.01, None)

    base_vol = rng.uniform(1e6, 5e7)
    volume = base_vol * (1.0 + 0.4 * np.abs(rng.normal(0.0, 1.0, size=n)))

    frame = pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": np.round(volume),
        },
        index=index,
    )
    return frame, close


def _synth_vix(
    index: pd.DatetimeIndex, seed: int, market_close: np.ndarray | None
) -> pd.DataFrame:
    """Mean-reverting VIX ~12..35, higher when the market proxy falls."""
    rng = np.random.default_rng(seed + _stable_hash(config.VIX_TICKER))
    n = len(index)

    level = 16.0
    theta = 0.15          # mean-reversion speed
    mean = 18.0
    vals = np.empty(n)
    for i in range(n):
        level += theta * (mean - level) + rng.normal(0.0, 1.6)
        level = float(np.clip(level, 9.0, 60.0))
        vals[i] = level

    # Tilt higher on market down-days so VIX inversely tracks the proxy.
    if market_close is not None and len(market_close) == n:
        rets = np.zeros(n)
        rets[1:] = np.diff(market_close) / market_close[:-1]
        vals = vals - 220.0 * rets
        vals = np.clip(vals, 9.0, 80.0)

    close = pd.Series(vals, index=index)
    frame = pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]).values,
            "High": (close * 1.03).values,
            "Low": (close * 0.97).values,
            "Close": close.values,
            "Volume": np.zeros(n),
        },
        index=index,
    )
    return frame


def synthetic_prices(
    tickers: list[str], days: int = 420, seed: int = 42
) -> dict[str, pd.DataFrame]:
    """Deterministic offline OHLCV for `tickers` (and ^VIX if requested).

    >= 420 bars so MA200 + 20d slope + 1-year vol are all defined.
    """
    days = max(days, 420)
    index = _business_index(days, _SYNTH_END)

    # Build the benchmark first so VIX can lean against it.
    benchmark = config.BENCHMARK
    market_close: np.ndarray | None = None
    out: dict[str, pd.DataFrame] = {}

    if benchmark in tickers:
        frame, market_close = _synth_equity(benchmark, index, seed)
        out[benchmark] = frame

    for ticker in tickers:
        if ticker in out:
            continue
        if ticker == config.VIX_TICKER:
            out[ticker] = _synth_vix(index, seed, market_close)
        else:
            frame, close = _synth_equity(ticker, index, seed)
            out[ticker] = frame
            if market_close is None:
                market_close = close

    return out
