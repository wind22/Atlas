"""历史相似状态测试 (方案 §7).

守住两件事：
  1. 检索正确——按 (T,R,广度,VIX) 距离找最像的历史日子，排除最近 ~1 个月，历史
     不足时优雅降级为空。
  2. 铁律 Ⅱ 红线——输出**只描述当时状态**，绝无任何前向 / 未来收益字段。这是本
     模块存在的全部意义，用测试钉死。
"""
from __future__ import annotations

from atlas.config import Layer
from atlas.report.similarity import build_similar
from atlas.types import (
    DimensionScores,
    DailyReport,
    Regime,
    RegimeState,
    TickerIndicators,
    TickerResult,
)


def _spy(T: float, R: float) -> TickerResult:
    ind = TickerIndicators(
        ticker="SPY", name="标普500", layer=Layer.MARKET,
        close=100.0, prev_close=100.0, ma50=100.0, ma200=100.0, ma200_prev=100.0,
        prev_ma50=100.0, prev_ma200=100.0, adx=20.0, macd_hist=0.0, rsi=50.0,
        prev_rsi=50.0, mom_12_1=0.0, ret_6m=0.0, rs_3m=0.0, high_52w=100.0,
        dist_to_52w_high=0.0, drawdown_60d=0.0, vol_20d=0.01, vol_1y_avg=0.01,
        volume=1.0, vol_avg_20=1.0, above_ma200=True, above_ma50=True,
        ma50_above_ma200=True, ma200_rising=True, golden_cross=False,
        death_cross=False, reclaimed_ma200=False, broke_ma200=False,
        is_new_52w_high=False,
    )
    return TickerResult(ticker="SPY", name="标普500", layer=Layer.MARKET, T=T, R=R,
                        dims=DimensionScores(0, 0, 0, 0.0), risk_flags=[], indicators=ind)


def _rep(date: str, *, T: float, R: float, breadth: float, vix: float,
         regime: Regime = Regime.RISK_ON) -> DailyReport:
    return DailyReport(
        date=date,
        market_regime=RegimeState(regime=regime, raw_regime=regime, prev_regime=None,
                                  changed=False, reason=""),
        breadth_pct=breadth, vix=vix, results={"SPY": _spy(T, R)}, alerts=[],
    )


def _history(n: int) -> list[DailyReport]:
    """n 天历史（date 降序，如 load_recent）。第 40 天故意造一个「像今天」的状态。"""
    out = []
    for i in range(1, n + 1):
        if i == 40:
            T, R, breadth, vix = 62.0, 20.0, 0.55, 16.0     # 贴近今天
        else:
            T, R, breadth, vix = 40.0, 50.0, 0.30, 28.0     # 远离今天
        out.append(_rep(f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                        T=T, R=R, breadth=breadth, vix=vix))
    return out


def test_finds_the_closest_historical_state():
    today = _rep("2026-07-09", T=63.0, R=19.0, breadth=0.56, vix=15.0)
    out = build_similar(today, _history(80), top_n=1)
    assert len(out["similar_periods"]) == 1
    top = out["similar_periods"][0]
    assert top["T_spy"] == 62.0 and top["R_spy"] == 20.0    # 命中第 40 天那条
    assert "distance" in top


def test_excludes_the_most_recent_month():
    # 只有最近 10 天有历史 → 全部落在 min_gap(21) 排除区 → 无匹配。
    today = _rep("2026-07-09", T=60.0, R=20.0, breadth=0.5, vix=18.0)
    out = build_similar(today, _history(10))
    assert out["similar_periods"] == []


def test_degrades_gracefully_without_spy():
    today = DailyReport(date="2026-07-09",
                        market_regime=RegimeState(Regime.RISK_ON, Regime.RISK_ON, None, False, ""),
                        breadth_pct=0.5, vix=18.0, results={}, alerts=[])
    assert build_similar(today, _history(80)) == {"similar_periods": []}


def test_top_n_capped_and_sorted_by_distance():
    today = _rep("2026-07-09", T=63.0, R=19.0, breadth=0.56, vix=15.0)
    out = build_similar(today, _history(120), top_n=3)
    dists = [p["distance"] for p in out["similar_periods"]]
    assert len(dists) == 3
    assert dists == sorted(dists)                            # ascending distance


# --------------------------------------------------------------------------
# 铁律 Ⅱ：绝无前向 / 未来收益字段
# --------------------------------------------------------------------------
def test_no_forward_looking_fields_anywhere():
    today = _rep("2026-07-09", T=63.0, R=19.0, breadth=0.56, vix=15.0)
    out = build_similar(today, _history(80), top_n=3)
    forbidden = ("next", "next_30d", "forward", "return", "ret_", "future",
                 "forecast", "expected", "outcome", "pnl", "gain")
    for period in out["similar_periods"]:
        for key in period:
            assert not any(bad in key.lower() for bad in forbidden), key
    # 字段白名单：只允许描述当时状态的键。
    allowed = {"date", "regime", "T_spy", "R_spy", "breadth_pct", "vix", "distance"}
    for period in out["similar_periods"]:
        assert set(period) <= allowed
