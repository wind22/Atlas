"""Offline mechanics test for the survival backtest (no network)."""
from atlas import backtest


def test_run_backtest_offline_structure():
    payload = backtest.run_backtest(["SPY", "QQQ"], offline=True, cost_bps=10)
    assert set(payload["tickers"]) == {"SPY", "QQQ"}
    assert payload["cost_bps"] == 10
    for t in ("SPY", "QQQ"):
        o = payload["tickers"][t]["overall"]
        s = o["strategies"]
        assert set(s) == {"buyhold", "atlas", "naive200"}
        bh, at = s["buyhold"], s["atlas"]
        # drawdowns are non-negative percentages; gating never deepens the drawdown
        assert 0 <= at["maxdd"] <= 100 and 0 <= bh["maxdd"] <= 100
        assert at["maxdd"] <= bh["maxdd"] + 1e-6
        assert o["dd_saved"] == round(bh["maxdd"] - at["maxdd"], 1)
        # every strategy carries the full risk-adjusted metric set
        for m in s.values():
            assert {"total", "cagr", "maxdd", "sharpe", "sortino", "ulcer", "mar"} <= set(m)
        assert "crises" in payload["tickers"][t]


def test_transaction_cost_reduces_gated_return():
    """Higher per-switch cost must not increase the gated strategy's return."""
    cheap = backtest.run_backtest(["QQQ"], offline=True, cost_bps=0)
    dear = backtest.run_backtest(["QQQ"], offline=True, cost_bps=50)
    a0 = cheap["tickers"]["QQQ"]["overall"]["strategies"]["atlas"]["total"]
    a1 = dear["tickers"]["QQQ"]["overall"]["strategies"]["atlas"]["total"]
    assert a1 <= a0 + 1e-6


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
