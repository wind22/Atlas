"""解释层测试 (方案 §5).

守住：headline 由制度规则决定；top_risks/opportunities 取自已排序 alerts 且不对称
（风险优先、各自封顶）；delta 只讲变化（制度切换排第一 + 新增 alert）；无前一日
时 delta 为空。铁律 Ⅱ：输出全为暴露/状态语言，无前向字段（键名由 test_artifacts
的禁字段扫描兜底，这里聚焦语义）。
"""
from __future__ import annotations

import pytest

from atlas.config import Layer
from atlas.report.explain import build_explain
from atlas.types import (
    Alert,
    AlertKind,
    DailyReport,
    Regime,
    RegimeState,
)


def _regime_state(regime: Regime, *, prev=None, changed=False, reason="") -> RegimeState:
    return RegimeState(
        regime=regime, raw_regime=regime, prev_regime=prev,
        changed=changed, reason=reason,
    )


def _report(regime: Regime, *, alerts=None, prev=None, changed=False, date="2026-07-08") -> DailyReport:
    return DailyReport(
        date=date,
        market_regime=_regime_state(regime, prev=prev, changed=changed),
        breadth_pct=0.5,
        vix=18.0,
        results={},
        alerts=list(alerts or []),
    )


def _risk(ticker, title, sev) -> Alert:
    return Alert(kind=AlertKind.RISK, ticker=ticker, layer=Layer.STOCK,
                 title=title, detail="", severity=sev)


def _opp(ticker, title, sev) -> Alert:
    return Alert(kind=AlertKind.OPPORTUNITY, ticker=ticker, layer=Layer.STOCK,
                 title=title, detail="", severity=sev)


# --------------------------------------------------------------------------
# headline
# --------------------------------------------------------------------------
@pytest.mark.parametrize("regime,needle", [
    (Regime.RISK_ON, "持有"),
    (Regime.CAUTION, "风险升高"),
    (Regime.RISK_OFF, "降低风险敞口"),
    (Regime.OVERSOLD, "不急于抄底"),
])
def test_headline_follows_regime(regime, needle):
    out = build_explain(_report(regime), None)
    assert needle in out["headline"]


def test_risk_off_headline_is_defensive_regardless_of_opportunities():
    # 铁律 Ⅰ：即便有机会类 alert，Risk-Off 的结论仍是降敞口。
    rep = _report(Regime.RISK_OFF, alerts=[_opp("NVDA", "金叉：50日上穿200日", 7)])
    out = build_explain(rep, None)
    assert "降低风险敞口" in out["headline"]


# --------------------------------------------------------------------------
# top_risks / top_opportunities
# --------------------------------------------------------------------------
def test_top_risks_sorted_by_severity_and_capped():
    alerts = [
        _risk("AAPL", "波动率突破近一年上沿", 5),
        _risk("NVDA", "跌破200日均线", 10),
        _risk("MSFT", "死叉：50日下穿200日", 8),
        _risk("TSLA", "深度回撤>20%", 6),
    ]
    out = build_explain(_report(Regime.CAUTION, alerts=alerts), None)
    assert len(out["top_risks"]) == 3                      # capped at 3
    assert out["top_risks"][0] == "英伟达：跌破200日均线"    # highest severity first
    assert out["top_risks"][1].endswith("死叉：50日下穿200日")


def test_labels_use_chinese_name_but_pseudo_ticker_uses_title_only():
    alerts = [
        _risk("BREADTH", "广度骤降（普涨转分化）", 7),
        _opp("NVDA", "放量创52周新高", 7),
    ]
    out = build_explain(_report(Regime.CAUTION, alerts=alerts), None)
    assert out["top_risks"] == ["广度骤降（普涨转分化）"]     # no name prefix
    assert out["top_opportunities"] == ["英伟达：放量创52周新高"]


def test_empty_when_no_alerts_of_a_kind():
    out = build_explain(_report(Regime.RISK_ON, alerts=[_risk("NVDA", "跌破200日均线", 10)]), None)
    assert out["top_risks"] == ["英伟达：跌破200日均线"]
    assert out["top_opportunities"] == []


# --------------------------------------------------------------------------
# delta_from_yesterday
# --------------------------------------------------------------------------
def test_delta_empty_without_previous():
    assert build_explain(_report(Regime.CAUTION), None)["delta_from_yesterday"] == []


def test_delta_leads_with_regime_switch():
    rep = _report(Regime.CAUTION, prev=Regime.RISK_ON, changed=True)
    prev = _report(Regime.RISK_ON, date="2026-07-07")
    out = build_explain(rep, prev)
    assert out["delta_from_yesterday"][0] == "市场制度：进攻区 Risk-On → 警戒区 Caution"


def test_delta_surfaces_new_alerts_risk_first():
    prev = _report(Regime.CAUTION, alerts=[_risk("NVDA", "跌破200日均线", 10)], date="2026-07-07")
    rep = _report(Regime.CAUTION, alerts=[
        _risk("NVDA", "跌破200日均线", 10),        # carried over → not "new"
        _opp("AMD", "金叉：50日上穿200日", 7),      # new opportunity
        _risk("MSFT", "死叉：50日下穿200日", 8),    # new risk
    ])
    out = build_explain(rep, prev)
    deltas = out["delta_from_yesterday"]
    assert "新增风险：微软：死叉：50日下穿200日" in deltas
    assert "新增机会：AMD：金叉：50日上穿200日" in deltas
    # risk listed before opportunity (不对称)
    assert deltas.index("新增风险：微软：死叉：50日下穿200日") < \
           deltas.index("新增机会：AMD：金叉：50日上穿200日")
    # carried-over alert is NOT reported as new
    assert not any("英伟达" in d for d in deltas)
