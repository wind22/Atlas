"""Market regime classification and N-day confirmation (architecture.md §4).

Maps the trend score T (油门) and risk score R (刹车) onto one of four
regimes, then suppresses single-day whipsaw via an N-day confirmation gate.

铁律 Ⅰ (防御优先): defense outranks offense. When R is high (or T is weak)
the ticker is defensive regardless of how strong the trend score looks —
「宁可错过，不可重伤」.
"""
from __future__ import annotations

from . import config
from .types import (
    REGIME_LABEL,
    REGIME_LIGHT,
    Regime,
    RegimeState,
    TickerResult,
)


def _fmt(x: float) -> str:
    """Format a score for display: drop the decimal when it is a whole number."""
    xf = float(x)
    return f"{xf:.0f}" if xf.is_integer() else f"{xf:.1f}"


def _defense_annotation(result: TickerResult) -> str:
    """Short Chinese note (in parentheses) of what is driving the defense."""
    ind = result.indicators
    parts: list[str] = []
    if not ind.above_ma200 or ind.broke_ma200:
        parts.append("跌破200线")
    if ind.death_cross:
        parts.append("死叉")
    if ind.drawdown_60d >= config.DRAWDOWN_SEVERE:
        parts.append("深度回撤")
    elif ind.drawdown_60d >= config.DRAWDOWN_WARN:
        parts.append("回撤")
    if ind.vol_1y_avg > 0 and ind.vol_20d > config.VOL_SPIKE_MULT * ind.vol_1y_avg:
        parts.append("波动飙升")
    if not parts:
        return ""
    return "（" + "/".join(parts) + "）"


def _stabilizing_annotation(result: TickerResult) -> str:
    """Short Chinese note of the 企稳 signal(s) that flip Risk-Off → Oversold."""
    ind = result.indicators
    parts: list[str] = []
    if ind.reclaimed_ma200:
        parts.append("重回200线")
    if ind.rsi > ind.prev_rsi and ind.prev_rsi < config.RSI_OVERSOLD:
        parts.append(f"RSI从{_fmt(ind.prev_rsi)}回升")
    return "（" + "/".join(parts) + "）" if parts else ""


def classify(result: TickerResult) -> tuple[Regime, str]:
    """Classify a ticker's raw (unconfirmed) regime from its T / R scores.

    Priority (架构 §4, 防御优先于进攻):
      1. Defensive: R >= R_HIGH or T <= T_WEAK.
         - with 企稳 signal  -> 🟠 超卖观察 (Oversold)
         - otherwise         -> 🔴 防御区   (Risk-Off)
      2. T >= T_STRONG and R <= R_LOW -> 🟢 进攻区 (Risk-On)
      3. otherwise                    -> 🟡 警戒区 (Caution)

    Returns (raw_regime, chinese_reason). The reason cites the numbers.
    """
    ind = result.indicators
    T = result.T
    R = result.R

    defensive = R >= config.R_HIGH or T <= config.T_WEAK
    if defensive:
        triggers: list[str] = []
        if R >= config.R_HIGH:
            triggers.append(f"R={_fmt(R)}≥{config.R_HIGH}")
        if T <= config.T_WEAK:
            triggers.append(f"T={_fmt(T)}≤{config.T_WEAK}")
        trigger = "、".join(triggers)

        stabilizing = ind.reclaimed_ma200 or (
            ind.rsi > ind.prev_rsi and ind.prev_rsi < config.RSI_OVERSOLD
        )
        if stabilizing:
            reason = f"{trigger}{_defense_annotation(result)}，但现企稳信号{_stabilizing_annotation(result)} → 超卖观察"
            return Regime.OVERSOLD, reason
        reason = f"{trigger}{_defense_annotation(result)} → 防御"
        return Regime.RISK_OFF, reason

    if T >= config.T_STRONG and R <= config.R_LOW:
        reason = f"T={_fmt(T)}≥{config.T_STRONG} 且 R={_fmt(R)}≤{config.R_LOW} → 进攻"
        return Regime.RISK_ON, reason

    reason = f"T={_fmt(T)}、R={_fmt(R)} 未达进攻（T≥{config.T_STRONG}且R≤{config.R_LOW}）或防御条件 → 警戒"
    return Regime.CAUTION, reason


def confirm(
    recent_raw: list[Regime], prev_confirmed: Regime | None
) -> RegimeState:
    """Apply the N-day confirmation gate to a stream of raw regimes (§4.1).

    ``recent_raw`` is most-recent-first (today at index 0). A switch to X is
    confirmed only when the most recent ``config.REGIME_CONFIRM_DAYS`` raw
    regimes are ALL == X and X differs from the previously confirmed regime.
    Otherwise the confirmed regime is held, trading a little 「反应慢」 for far
    fewer false switches (铁律 Ⅲ).
    """
    today = recent_raw[0]

    if prev_confirmed is None:
        reason = f"{REGIME_LIGHT[today]} {REGIME_LABEL[today]}（首次记录）"
        return RegimeState(
            regime=today,
            raw_regime=today,
            prev_regime=None,
            changed=False,
            reason=reason,
        )

    n = config.REGIME_CONFIRM_DAYS
    switch_confirmed = (
        today != prev_confirmed
        and len(recent_raw) >= n
        and all(r == today for r in recent_raw[:n])
    )

    if switch_confirmed:
        confirmed = today
        changed = True
    else:
        confirmed = prev_confirmed
        changed = False

    reason = f"{REGIME_LIGHT[confirmed]} {REGIME_LABEL[confirmed]}"
    if changed:
        reason += f"（较昨日：{REGIME_LIGHT[prev_confirmed]}→{REGIME_LIGHT[confirmed]}）"
    elif today != confirmed:
        reason += (
            f"（今日 raw={REGIME_LIGHT[today]}{REGIME_LABEL[today]}，"
            f"未满{n}日确认，维持原制度）"
        )

    return RegimeState(
        regime=confirmed,
        raw_regime=today,
        prev_regime=prev_confirmed,
        changed=changed,
        reason=reason,
    )
