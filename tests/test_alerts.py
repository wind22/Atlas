"""Black-box tests for atlas.alerts (architecture.md §5)."""
from __future__ import annotations

from atlas import alerts
from atlas.types import AlertKind


def test_default_indicators_raise_no_alerts(make_result):
    # Sanity: the clean-uptrend default must not fire spurious alerts.
    assert alerts.detect_alerts(make_result(), None) == []


def test_broke_ma200_is_a_risk_alert(make_result):
    result = make_result(broke_ma200=True)
    out = alerts.detect_alerts(result, None)
    assert any(a.kind is AlertKind.RISK for a in out)


def test_golden_cross_is_an_opportunity_alert(make_result):
    result = make_result(golden_cross=True)
    out = alerts.detect_alerts(result, None)
    assert any(a.kind is AlertKind.OPPORTUNITY for a in out)


def test_new_high_on_volume_is_an_opportunity_alert(make_result):
    result = make_result(
        is_new_52w_high=True,
        volume=2_000_000.0,
        vol_avg_20=1_000_000.0,  # volume > 1.5x avg -> breakout
    )
    out = alerts.detect_alerts(result, None)
    assert any(a.kind is AlertKind.OPPORTUNITY for a in out)


def test_new_high_without_volume_no_breakout_alert(make_result):
    result = make_result(
        is_new_52w_high=True,
        volume=1_000_000.0,
        vol_avg_20=1_000_000.0,  # not > 1.5x -> no breakout alert
    )
    out = alerts.detect_alerts(result, None)
    titles = " ".join(a.title for a in out)
    assert "52" not in titles  # no 放量创52周新高 alert


def test_detect_breadth_alert_fires_on_collapse():
    alert = alerts.detect_breadth_alert(0.30, 0.65)
    assert alert is not None
    assert alert.kind is AlertKind.RISK


def test_detect_breadth_alert_none_when_stable():
    assert alerts.detect_breadth_alert(0.62, 0.65) is None
