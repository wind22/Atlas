"""Price data acquisition (架构 §2 数据层).

Two ways in:
  * fetch_prices / fetch_vix  — live pull. Tries yfinance (Yahoo) first, then
    falls back to Stooq's free CSV feed if Yahoo fails or rate-limits. Both
    yield auto-adjusted OHLCV. Pass source="yfinance" / "stooq" to force one.
  * synthetic_prices          — deterministic offline generator so the whole
    pipeline runs with no network and reproducible results (workflow runtime
    forbids wall-clock, so the synthetic calendar ends on a FIXED date).

Every DataFrame returned here has an ascending DatetimeIndex and the columns
Open, High, Low, Close, Volume (closes already adjusted).
"""
from __future__ import annotations

import hashlib
import io
import sys
import urllib.request

import numpy as np
import pandas as pd

from . import config

# Data sources, in fallback order for source="auto".
SOURCE_AUTO = "auto"
SOURCE_YF = "yfinance"
SOURCE_STOOQ = "stooq"

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


def _fetch_yf_one(ticker: str, period: str) -> pd.DataFrame | None:
    """One ticker via yfinance; None on failure/empty (no exceptions escape)."""
    try:
        import yfinance as yf

        raw = yf.download(
            ticker,
            period=period,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception as exc:  # noqa: BLE001 — resilience over precision
        _warn(f"{ticker}: yfinance failed ({exc!r})")
        return None
    return _normalize(raw, ticker)


def _stooq_symbol(ticker: str) -> str:
    """Map a Yahoo-style ticker to a Stooq symbol (SPY→spy.us, ^VIX→^vix)."""
    if ticker.startswith("^"):
        return ticker.lower()
    return f"{ticker.lower()}.us"


def _period_to_timedelta(period: str) -> pd.Timedelta | None:
    p = period.strip().lower()
    try:
        if p.endswith("mo"):
            return pd.Timedelta(days=int(float(p[:-2]) * 30.5))
        if p.endswith("y"):
            return pd.Timedelta(days=int(float(p[:-1]) * 365.25))
        if p.endswith("d"):
            return pd.Timedelta(days=int(p[:-1]))
    except ValueError:
        pass
    return None


def _slice_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Keep only the trailing `period` window (Stooq returns full history)."""
    td = _period_to_timedelta(period)
    if td is None or len(df) == 0:
        return df
    sliced = df[df.index >= (df.index.max() - td)]
    return sliced if len(sliced) else df


def _parse_stooq_csv(raw: str, ticker: str, period: str) -> pd.DataFrame | None:
    """Parse a Stooq daily-CSV body into ascending OHLCV; None if unusable."""
    # Stooq returns "<html>… No data" or an empty body for unknown symbols.
    if not raw.strip() or raw.lstrip()[0] == "<" or "No data" in raw:
        return None
    try:
        df = pd.read_csv(io.StringIO(raw))
    except Exception:  # noqa: BLE001
        return None
    if "Date" not in df.columns or "Close" not in df.columns:
        return None
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).set_index("Date")
    for col in _OHLCV:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    norm = _normalize(df, ticker)
    return None if norm is None else _slice_period(norm, period)


def _fetch_stooq_one(ticker: str, period: str) -> pd.DataFrame | None:
    """One ticker via Stooq's free daily CSV; None on failure/empty."""
    url = f"https://stooq.com/q/d/l/?s={_stooq_symbol(ticker)}&i=d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Atlas)"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        _warn(f"{ticker}: stooq failed ({exc!r})")
        return None
    return _parse_stooq_csv(raw, ticker, period)


def fetch_prices(
    tickers: list[str],
    period: str = config.DEFAULT_PERIOD,
    source: str = SOURCE_AUTO,
) -> dict[str, pd.DataFrame]:
    """Adjusted OHLCV per ticker; yfinance→Stooq fallback. Drop failures.

    source: "auto" (yfinance then Stooq), "yfinance", or "stooq".
    """
    out: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        norm: pd.DataFrame | None = None
        if source in (SOURCE_AUTO, SOURCE_YF):
            norm = _fetch_yf_one(ticker, period)
        if norm is None and source in (SOURCE_AUTO, SOURCE_STOOQ):
            norm = _fetch_stooq_one(ticker, period)
            if norm is not None and source == SOURCE_AUTO:
                _warn(f"{ticker}: Yahoo unavailable, used Stooq fallback ({len(norm)} bars)")
        if norm is None:
            _warn(f"{ticker}: no usable data from any source; skipping")
            continue
        out[ticker] = norm
    return out


def fetch_vix(
    period: str = config.DEFAULT_PERIOD, source: str = SOURCE_AUTO
) -> pd.DataFrame | None:
    """Fetch the VIX (config.VIX_TICKER) via the same fallback chain; None if all fail."""
    got = fetch_prices([config.VIX_TICKER], period=period, source=source)
    return got.get(config.VIX_TICKER)


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
