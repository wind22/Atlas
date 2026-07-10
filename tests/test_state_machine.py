"""制度状态机测试 (方案 §6).

守住：days_in_regime 数当前制度的连续天数（含今日）；previous_regime 与
last_transition_date 指向可见历史里最近一次切换；窗口内始终同一制度时诚实标未知；
首日（无历史）优雅降级。铁律 Ⅱ：只描述已发生状态，无任何前向字段。
"""
from __future__ import annotations

from atlas.report.state_machine import build_state
from atlas.types import DailyReport, Regime, RegimeState


def _rep(date: str, regime: Regime, *, prev=None, changed=False, reason="") -> DailyReport:
    return DailyReport(
        date=date,
        market_regime=RegimeState(
            regime=regime, raw_regime=regime, prev_regime=prev,
            changed=changed, reason=reason,
        ),
        breadth_pct=0.5, vix=18.0, results={}, alerts=[],
    )


def test_first_day_no_history():
    today = _rep("2026-07-06", Regime.RISK_ON, reason="首次记录")
    st = build_state(today, [])
    assert st["current_regime"] == "risk_on"
    assert st["days_in_regime"] == 1
    assert st["previous_regime"] is None
    assert st["last_transition_date"] is None
    assert st["transition_reason"] is None


def test_counts_consecutive_days_same_regime():
    today = _rep("2026-07-09", Regime.RISK_ON)
    recent = [  # date DESC, as snapshot.load_recent returns
        _rep("2026-07-08", Regime.RISK_ON),
        _rep("2026-07-07", Regime.RISK_ON),
        _rep("2026-07-06", Regime.CAUTION),
    ]
    st = build_state(today, recent)
    assert st["days_in_regime"] == 3                     # 09, 08, 07
    assert st["previous_regime"] == "caution"
    assert st["last_transition_date"] == "2026-07-07"    # first day of the RISK_ON run


def test_transition_reason_comes_from_the_switch_day():
    today = _rep("2026-07-09", Regime.CAUTION)
    recent = [
        _rep("2026-07-08", Regime.CAUTION, prev=Regime.RISK_ON, changed=True,
             reason="R 升破 60，防御优先"),
        _rep("2026-07-07", Regime.RISK_ON),
    ]
    st = build_state(today, recent)
    assert st["days_in_regime"] == 2                     # 09, 08
    assert st["previous_regime"] == "risk_on"
    assert st["last_transition_date"] == "2026-07-08"
    assert st["transition_reason"] == "R 升破 60，防御优先"


def test_switch_today_is_one_day_in_regime():
    today = _rep("2026-07-09", Regime.CAUTION, prev=Regime.RISK_ON, changed=True,
                 reason="宽度走弱")
    recent = [_rep("2026-07-08", Regime.RISK_ON), _rep("2026-07-07", Regime.RISK_ON)]
    st = build_state(today, recent)
    assert st["days_in_regime"] == 1
    assert st["previous_regime"] == "risk_on"
    assert st["last_transition_date"] == "2026-07-09"
    assert st["transition_reason"] == "宽度走弱"


def test_unchanged_across_whole_window_marks_transition_unknown():
    today = _rep("2026-07-09", Regime.RISK_ON)
    recent = [_rep("2026-07-08", Regime.RISK_ON), _rep("2026-07-07", Regime.RISK_ON)]
    st = build_state(today, recent)
    assert st["days_in_regime"] == 3
    assert st["previous_regime"] is None                 # no switch within window
    assert st["last_transition_date"] is None            # → honestly unknown
    assert st["transition_reason"] is None


def test_no_forward_looking_keys():
    st = build_state(_rep("2026-07-09", Regime.RISK_ON), [])
    forbidden = {"next", "forecast", "prediction", "expected", "will", "future"}
    for k in st:
        assert not any(bad in k.lower() for bad in forbidden), k
