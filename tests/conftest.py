"""Shared pytest fixtures / factories for the Atlas test suite.

Black-box helpers only: they build the public data contracts from atlas.types
(TickerIndicators / TickerResult) and deterministic OHLCV frames, with FIXED
seeds and a FIXED business-day calendar (no wall-clock dates), so every test is
reproducible regardless of when it runs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from atlas.config import Layer
from atlas.types import DimensionScores, TickerIndicators, TickerResult

# Fixed anchor date — the last bar of every synthetic frame. Never today().
LAST_DATE = "2026-07-08"


# --------------------------------------------------------------------------
# synthetic OHLCV
# --------------------------------------------------------------------------
def _business_index(days: int) -> pd.DatetimeIndex:
    """Ascending business-day index of length ``days`` ending on LAST_DATE."""
    return pd.bdate_range(end=LAST_DATE, periods=days)


def _synthetic_ohlcv(trend: str, days: int = 420) -> pd.DataFrame:
    """Deterministic OHLCV frame for a given trend.

    trend:
      "up"   — steady uptrend: close well above a rising MA200 (bullish stack).
      "down" — steady downtrend: close below MA200, 50<200 (death-cross stack).
      "flat" — choppy sideways: no durable trend.

    Columns: Open, High, Low, Close, Volume. Index: ascending business days
    ending LAST_DATE. Uses numpy.default_rng with a fixed seed.
    """
    idx = _business_index(days)
    rng = np.random.default_rng(42)
    i = np.arange(days, dtype=float)

    if trend == "up":
        # Strong positive drift dominates the noise -> MA200 rises, close above.
        close = 100.0 + 0.6 * i + rng.normal(0.0, 0.5, days)
    elif trend == "down":
        # Strong negative drift -> close below MA200, 50<200.
        close = 320.0 - 0.6 * i + rng.normal(0.0, 0.5, days)
    elif trend == "flat":
        # Oscillation around a level with noise -> chop, no durable trend.
        close = 200.0 + 6.0 * np.sin(i / 9.0) + rng.normal(0.0, 1.0, days)
    else:  # pragma: no cover - guard
        raise ValueError(f"unknown trend: {trend!r}")

    close = np.maximum(close, 1.0)
    close = pd.Series(close, index=idx)

    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) * 1.005
    low = pd.concat([open_, close], axis=1).min(axis=1) * 0.995
    volume = pd.Series(1_000_000.0 + rng.normal(0.0, 10_000.0, days), index=idx).abs()

    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


@pytest.fixture
def synthetic_ohlcv():
    """Return the synthetic OHLCV factory: synthetic_ohlcv(trend, days=420)."""
    return _synthetic_ohlcv


# --------------------------------------------------------------------------
# TickerIndicators factory
# --------------------------------------------------------------------------
def _make_indicators(**overrides) -> TickerIndicators:
    """Construct a TickerIndicators for a clean, healthy uptrend by default.

    Defaults trigger NO alerts and score a full trend, so a test can flip a
    single field to isolate the behavior it cares about.
    """
    defaults = dict(
        ticker="TEST",
        name="测试",
        layer=Layer.MARKET,
        close=110.0,
        prev_close=109.0,
        ma50=105.0,
        ma200=100.0,
        ma200_prev=98.0,
        prev_ma50=104.5,
        prev_ma200=99.5,
        adx=30.0,
        macd_hist=0.5,
        rsi=55.0,
        prev_rsi=55.0,
        mom_12_1=0.20,
        ret_6m=0.10,
        rs_3m=0.05,
        high_52w=111.0,
        dist_to_52w_high=0.01,
        drawdown_60d=0.0,
        vol_20d=0.01,
        vol_1y_avg=0.01,
        volume=1_000_000.0,
        vol_avg_20=1_000_000.0,
        above_ma200=True,
        above_ma50=True,
        ma50_above_ma200=True,
        ma200_rising=True,
        golden_cross=False,
        death_cross=False,
        reclaimed_ma200=False,
        broke_ma200=False,
        is_new_52w_high=False,
        vix=None,
        prev_vix=None,
    )
    defaults.update(overrides)
    return TickerIndicators(**defaults)


@pytest.fixture
def make_indicators():
    """Return the TickerIndicators factory: make_indicators(**overrides)."""
    return _make_indicators


# --------------------------------------------------------------------------
# TickerResult factory
# --------------------------------------------------------------------------
def _make_result(
    *,
    T: float = 100.0,
    R: float = 0.0,
    ticker: str = "TEST",
    name: str = "测试",
    layer: Layer = Layer.MARKET,
    risk_flags=None,
    **ind_overrides,
) -> TickerResult:
    """Build a TickerResult wrapping make_indicators(**ind_overrides)."""
    ind_overrides.setdefault("ticker", ticker)
    ind_overrides.setdefault("name", name)
    ind_overrides.setdefault("layer", layer)
    ind = _make_indicators(**ind_overrides)
    dims = DimensionScores(direction=0, momentum=0, strength=0, breadth=0.0)
    return TickerResult(
        ticker=ticker,
        name=name,
        layer=layer,
        T=float(T),
        R=float(R),
        dims=dims,
        risk_flags=list(risk_flags or []),
        indicators=ind,
    )


@pytest.fixture
def make_result():
    """Return the TickerResult factory: make_result(T=.., R=.., **ind_overrides)."""
    return _make_result
