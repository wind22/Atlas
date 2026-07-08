"""Black-box tests for atlas.regime (architecture.md §4)."""
from __future__ import annotations

from atlas import config, regime
from atlas.types import Regime


def test_classify_risk_on(make_result):
    result = make_result(T=80, R=10)
    reg, reason = regime.classify(result)
    assert reg is Regime.RISK_ON
    assert reason  # a non-empty Chinese reason


def test_classify_risk_off_defense_outranks_offense(make_result):
    # High R must force defense even though T is strong.
    result = make_result(T=80, R=70)
    reg, _reason = regime.classify(result)
    assert reg is Regime.RISK_OFF


def test_classify_caution(make_result):
    result = make_result(T=50, R=40)
    reg, _reason = regime.classify(result)
    assert reg is Regime.CAUTION


def test_classify_oversold_when_defensive_but_stabilizing(make_result):
    # Defensive (R high) but a 企稳 signal: RSI rising from oversold + reclaim.
    result = make_result(
        T=80,
        R=70,
        reclaimed_ma200=True,
        prev_rsi=25.0,
        rsi=35.0,
    )
    reg, _reason = regime.classify(result)
    assert reg is Regime.OVERSOLD


def test_confirm_first_record_no_change():
    state = regime.confirm([Regime.RISK_ON], None)
    assert state.regime is Regime.RISK_ON
    assert state.changed is False
    assert state.prev_regime is None


def test_confirm_requires_consecutive_days_to_switch():
    n = config.REGIME_CONFIRM_DAYS
    # today differs but not yet N consecutive -> hold prior confirmed regime.
    recent = [Regime.CAUTION] + [Regime.RISK_ON] * n  # most-recent-first
    held = regime.confirm(recent, Regime.RISK_ON)
    assert held.regime is Regime.RISK_ON
    assert held.changed is False
    assert held.raw_regime is Regime.CAUTION

    # N consecutive identical raw regimes -> switch confirmed.
    switched = regime.confirm([Regime.CAUTION] * n, Regime.RISK_ON)
    assert switched.regime is Regime.CAUTION
    assert switched.changed is True


def test_confirm_single_contrary_day_does_not_flip():
    n = config.REGIME_CONFIRM_DAYS
    recent = [Regime.RISK_OFF] + [Regime.RISK_ON] * n
    state = regime.confirm(recent, Regime.RISK_ON)
    assert state.regime is Regime.RISK_ON
    assert state.changed is False
