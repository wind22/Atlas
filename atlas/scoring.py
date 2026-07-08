"""Scoring: turn TickerIndicators into trend score T and risk score R.

Implements architecture.md §3 exactly. Every point value is imported from
atlas.config — nothing is tuned here (铁律 Ⅱ). T = direction + momentum +
strength + breadth (0–100); R accumulates the §3.5 否决式清单 (0–100, capped).
"""
from __future__ import annotations

from . import config
from .config import Layer
from .types import DimensionScores, TickerIndicators, TickerResult


def score_direction(ind: TickerIndicators) -> int:
    """趋势方向分 (§3.1), 0..40."""
    s = 0
    if ind.above_ma200:
        s += config.DIR_ABOVE_MA200
    if ind.above_ma50:
        s += config.DIR_ABOVE_MA50
    if ind.ma50_above_ma200:
        s += config.DIR_MA50_ABOVE_MA200
    if ind.ma200_rising:
        s += config.DIR_MA200_RISING
    return s


def score_momentum(ind: TickerIndicators) -> int:
    """动量分 (§3.2), 0..30."""
    s = 0
    if ind.mom_12_1 > 0:
        s += config.MOM_12_1_POS
    if ind.ret_6m > 0:
        s += config.MOM_6M_POS
    if ind.rs_3m > 0:
        s += config.MOM_RS_POS
    if ind.dist_to_52w_high <= config.NEAR_HIGH_PCT:
        s += config.MOM_NEAR_HIGH
    return s


def score_strength(ind: TickerIndicators) -> int:
    """趋势强度分 (§3.3), 0..15."""
    s = 0
    if ind.adx >= config.ADX_TREND:
        s += config.STR_ADX
    if ind.macd_hist > 0:
        s += config.STR_MACD
    return s


def score_breadth(breadth_pct: float) -> float:
    """广度分 (§3.4), 0..15, linear from BREADTH_ZERO→0 to BREADTH_FULL→15.

    Endpoints snap exactly (≥60% → full 15, ≤20% → 0) per §3.4, both for
    spec fidelity and to avoid float artifacts at the band edges.
    """
    if breadth_pct >= config.BREADTH_FULL:
        return float(config.W_BREADTH)
    if breadth_pct <= config.BREADTH_ZERO:
        return 0.0
    span = config.BREADTH_FULL - config.BREADTH_ZERO
    raw = config.W_BREADTH * (breadth_pct - config.BREADTH_ZERO) / span
    return max(0.0, min(float(config.W_BREADTH), raw))


def score_risk(
    ind: TickerIndicators, *, vix: float | None, breadth_pct: float | None
) -> tuple[float, list[str]]:
    """风险分 R (§3.5) + Chinese flags. VIX rule applies only when vix is not None."""
    r = 0.0
    flags: list[str] = []

    if not ind.above_ma200:
        r += config.RISK_BELOW_MA200
        flags.append("跌破200日均线")

    if ind.ma50 < ind.ma200:
        r += config.RISK_DEATH_CROSS
        flags.append("死叉：50日<200日")

    if ind.drawdown_60d > config.DRAWDOWN_WARN:
        r += config.RISK_DRAWDOWN
        flags.append("自高点回撤 >10%")
        if ind.drawdown_60d > config.DRAWDOWN_SEVERE:
            r += config.RISK_DRAWDOWN_SEVERE_EXTRA
            flags.append("自高点回撤 >20%")

    if ind.vol_20d > ind.vol_1y_avg * config.VOL_SPIKE_MULT:
        r += config.RISK_VOL_SPIKE
        flags.append("波动骤放")

    if vix is not None:
        if vix > config.VIX_ELEVATED:
            r += config.RISK_VIX
            flags.append("VIX>20")
            if vix > config.VIX_PANIC:
                r += config.RISK_VIX_PANIC_EXTRA
                flags.append("VIX>30")

    if breadth_pct is not None and breadth_pct < config.BREADTH_WEAK:
        r += config.RISK_BREADTH_WEAK
        flags.append("广度<40%")

    r = min(r, float(config.RISK_CAP))
    return r, flags


def score_ticker(
    ind: TickerIndicators, *, breadth_pct: float, vix: float | None
) -> TickerResult:
    """Assemble T, R, dimension breakdown and flags into a TickerResult."""
    direction = score_direction(ind)
    momentum = score_momentum(ind)
    strength = score_strength(ind)
    breadth = score_breadth(breadth_pct)

    dims = DimensionScores(
        direction=direction,
        momentum=momentum,
        strength=strength,
        breadth=breadth,
    )
    t = float(direction + momentum + strength + breadth)

    # VIX only informs risk for the market / multi-asset layers (§3.5).
    risk_vix = vix if ind.layer in (Layer.MARKET, Layer.MULTI_ASSET) else None
    r, flags = score_risk(ind, vix=risk_vix, breadth_pct=breadth_pct)

    return TickerResult(
        ticker=ind.ticker,
        name=ind.name,
        layer=ind.layer,
        T=t,
        R=r,
        dims=dims,
        risk_flags=flags,
        indicators=ind,
    )
