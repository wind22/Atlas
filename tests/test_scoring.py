"""Black-box tests for atlas.scoring (architecture.md §3)."""
from __future__ import annotations

from atlas import scoring


def test_uptrend_direction_full_and_high_T(make_indicators):
    ind = make_indicators()  # healthy uptrend by default
    assert scoring.score_direction(ind) == 40
    result = scoring.score_ticker(ind, breadth_pct=0.60, vix=None)
    assert result.T >= 80  # strong trend across dimensions


def test_downtrend_risk_flags_and_high_R(make_indicators):
    ind = make_indicators(
        above_ma200=False,
        above_ma50=False,
        ma50_above_ma200=False,
        ma200_rising=False,
        ma50=95.0,
        ma200=100.0,
        drawdown_60d=0.25,
    )
    r, flags = scoring.score_risk(ind, vix=None, breadth_pct=None)
    assert any("200" in f for f in flags)  # 跌破200日均线 reason present
    assert r >= 40  # below-200 + death-cross + drawdown drive R high


def test_score_breadth_linear_bands():
    assert scoring.score_breadth(0.60) == 15
    assert scoring.score_breadth(0.20) == 0
    assert abs(scoring.score_breadth(0.40) - 7.5) < 1e-9


def test_score_breadth_clamped_outside_range():
    assert scoring.score_breadth(0.90) == 15
    assert scoring.score_breadth(0.05) == 0


def test_score_risk_caps_at_100(make_indicators):
    ind = make_indicators(
        above_ma200=False,
        ma50=90.0,
        ma200=100.0,
        drawdown_60d=0.30,
        vol_20d=1.0,
        vol_1y_avg=0.01,
    )
    r, _flags = scoring.score_risk(ind, vix=35.0, breadth_pct=0.30)
    assert r == 100


def test_score_direction_zero_when_all_false(make_indicators):
    ind = make_indicators(
        above_ma200=False,
        above_ma50=False,
        ma50_above_ma200=False,
        ma200_rising=False,
    )
    assert scoring.score_direction(ind) == 0


def test_score_momentum_and_strength_bounds(make_indicators):
    ind = make_indicators()
    assert 0 <= scoring.score_momentum(ind) <= 30
    assert 0 <= scoring.score_strength(ind) <= 15
