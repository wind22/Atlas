"""Black-box tests for atlas.indicators (architecture.md §3)."""
from __future__ import annotations

from atlas import indicators
from atlas.config import Layer


def test_uptrend_is_above_and_rising_ma200(synthetic_ohlcv):
    df = synthetic_ohlcv("up")
    bench = synthetic_ohlcv("up")
    ind = indicators.compute_indicators(
        df, bench, ticker="SPY", name="标普500", layer=Layer.MARKET
    )
    assert ind.above_ma200 is True
    assert ind.ma200_rising is True
    assert ind.ma50_above_ma200 is True
    assert 0.0 <= ind.rsi <= 100.0


def test_downtrend_is_below_ma200_and_death_stack(synthetic_ohlcv):
    df = synthetic_ohlcv("down")
    bench = synthetic_ohlcv("up")
    ind = indicators.compute_indicators(
        df, bench, ticker="SPY", name="标普500", layer=Layer.MARKET
    )
    assert ind.above_ma200 is False
    assert ind.ma50 < ind.ma200
    assert 0.0 <= ind.rsi <= 100.0


def test_rsi_always_bounded(synthetic_ohlcv):
    for trend in ("up", "down", "flat"):
        ind = indicators.compute_indicators(
            synthetic_ohlcv(trend),
            synthetic_ohlcv("up"),
            ticker="X",
            name="X",
            layer=Layer.STOCK,
        )
        assert 0.0 <= ind.rsi <= 100.0
        assert 0.0 <= ind.prev_rsi <= 100.0
        assert ind.dist_to_52w_high >= 0.0
        assert ind.drawdown_60d >= 0.0


def test_compute_breadth_fraction(synthetic_ohlcv):
    # 3 sectors above their MA200 (up), 2 below (down) -> 3/5 = 0.60.
    frames = {
        "A": synthetic_ohlcv("up"),
        "B": synthetic_ohlcv("up"),
        "C": synthetic_ohlcv("up"),
        "D": synthetic_ohlcv("down"),
        "E": synthetic_ohlcv("down"),
    }
    breadth = indicators.compute_breadth(frames)
    assert abs(breadth - 0.60) < 1e-9


def test_compute_breadth_empty_is_zero():
    assert indicators.compute_breadth({}) == 0.0
