"""Offline mechanics test for the survival backtest (no network)."""
from atlas import backtest


def test_run_backtest_offline_structure():
    payload = backtest.run_backtest(["SPY", "QQQ"], offline=True)
    assert set(payload["tickers"]) == {"SPY", "QQQ"}
    for t in ("SPY", "QQQ"):
        o = payload["tickers"][t]["overall"]
        # drawdowns are non-negative percentages; gating never deepens the drawdown
        assert 0 <= o["gated_maxdd"] <= 100
        assert 0 <= o["bh_maxdd"] <= 100
        assert o["gated_maxdd"] <= o["bh_maxdd"] + 1e-6
        assert o["dd_saved"] == round(o["bh_maxdd"] - o["gated_maxdd"], 1)
        assert "crises" in payload["tickers"][t]


def test_render_html_is_self_contained():
    payload = backtest.run_backtest(["SPY"], offline=True)
    html = backtest.render_html(payload)
    assert html.startswith("<!DOCTYPE html>") and html.rstrip().endswith("</html>")
    assert "<svg" in html
    # fully self-contained: no external resource references
    assert "http://" not in html and "https://" not in html


def test_exposure_mapping_defensive_is_flat():
    # Risk-Off must be fully de-risked (exposure 0) — 铁律 Ⅰ.
    from atlas.types import Regime
    assert backtest._EXPOSURE[Regime.RISK_OFF] == 0.0
    assert backtest._EXPOSURE[Regime.RISK_ON] == 1.0
