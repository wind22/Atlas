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


def test_max_drawdown_clamped_to_100pct():
    import pandas as pd
    # a corrupt equity path (bad tick → negative) must not report >100% drawdown
    assert backtest._max_drawdown(pd.Series([1.0, 5.0, -2.0, 3.0])) == 1.0
    assert 0.0 <= backtest._max_drawdown(pd.Series([1.0, 2.0, 0.001, 1.5])) <= 1.0


def test_clean_prices_removes_isolated_spike():
    import pandas as pd
    idx = pd.bdate_range("2024-01-01", periods=30)
    df = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0,
                       "Close": 100.0, "Volume": 1.0}, index=idx)
    df.iloc[15, df.columns.get_loc("Close")] = 1000.0   # 10x bad tick
    cleaned = backtest._clean_prices(df)
    assert cleaned["Close"].max() < 200                 # spike interpolated away


def test_multi_ticker_report_has_nav_and_anchors():
    payload = backtest.run_backtest(["SPY", "QQQ", "AAPL", "MSFT"], offline=True)
    html = backtest.render_html(payload)
    assert 'class="nav"' in html                       # index shown for many tickers
    for t in ("SPY", "QQQ", "AAPL", "MSFT"):           # one anchored section per ticker
        assert f'id="{backtest._anchor(t)}"' in html
    assert 'href="index.html"' in html                 # back to dashboard


def test_tradeoff_overview_present_for_multi_ticker():
    payload = backtest.run_backtest(["SPY", "QQQ", "AAPL", "MSFT"], offline=True)
    html = backtest.render_html(payload)
    # 收益 vs 回撤总览：仅多标的时出现，含散点图与汇总
    assert 'id="bt-tradeoff"' in html
    assert "收益 vs 回撤" in html and "回撤改善" in html
    assert html.count("<svg") >= 5          # 4 只资金曲线 + 1 张权衡散点图
    assert 'href="#bt-tradeoff"' in html     # 导航可跳转（>3 标的时显示索引）
    # 单标的不渲染总览
    solo = backtest.render_html(backtest.run_backtest(["SPY"], offline=True))
    assert 'id="bt-tradeoff"' not in solo


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
