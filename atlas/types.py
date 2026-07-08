"""Shared data contracts. This module is the single source of truth for the
shapes that flow between modules. Do NOT redefine these fields elsewhere.

Pipeline:  data_fetch -> indicators (TickerIndicators)
           -> scoring (TickerResult) -> regime (RegimeState) + alerts (Alert)
           -> assembled into DailyReport -> snapshot + dashboard.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

from .config import Layer


class Regime(str, Enum):
    """Market regime (架构 §4). The final conclusion, shown as one light."""

    RISK_ON = "risk_on"      # 🟢 进攻区
    CAUTION = "caution"      # 🟡 警戒区
    RISK_OFF = "risk_off"    # 🔴 防御区
    OVERSOLD = "oversold"    # 🟠 超卖观察


REGIME_LIGHT: dict[Regime, str] = {
    Regime.RISK_ON: "🟢",
    Regime.CAUTION: "🟡",
    Regime.RISK_OFF: "🔴",
    Regime.OVERSOLD: "🟠",
}

REGIME_LABEL: dict[Regime, str] = {
    Regime.RISK_ON: "进攻区 Risk-On",
    Regime.CAUTION: "警戒区 Caution",
    Regime.RISK_OFF: "防御区 Risk-Off",
    Regime.OVERSOLD: "超卖观察 Oversold",
}

REGIME_STANCE: dict[Regime, str] = {
    Regime.RISK_ON: "顺势持有，可关注强势行业与创新高个股",
    Regime.CAUTION: "减少新增仓位，收紧止损，只留最强标的",
    Regime.RISK_OFF: "降低风险敞口，转向现金 / 债券 / 黄金",
    Regime.OVERSOLD: "不急于抄底，观察趋势能否重建",
}


class AlertKind(str, Enum):
    RISK = "risk"            # 风险类提示
    OPPORTUNITY = "opportunity"  # 机会类提示


@dataclass
class TickerIndicators:
    """All indicator values needed downstream, latest bar unless noted.

    Populated by indicators.compute_indicators(). Returns/ratios are decimals
    (0.05 == 5%). Drawdown and dist_to_52w_high are non-negative magnitudes.
    """

    ticker: str
    name: str
    layer: Layer

    close: float
    prev_close: float

    ma50: float
    ma200: float
    ma200_prev: float          # MA200 value MA_SLOPE_LOOKBACK days ago
    prev_ma50: float           # yesterday's MA50 (for cross detection)
    prev_ma200: float          # yesterday's MA200

    adx: float
    macd_hist: float           # MACD histogram (DIF - DEA); >0 == bullish
    rsi: float
    prev_rsi: float

    mom_12_1: float            # 12-1 month momentum (decimal return)
    ret_6m: float              # 6-month return (decimal)
    rs_3m: float               # 3-month return minus benchmark's (excess)
    high_52w: float
    dist_to_52w_high: float    # (high_52w - close) / high_52w, >= 0

    drawdown_60d: float        # (peak60 - close) / peak60, >= 0
    vol_20d: float             # std of daily returns over 20d
    vol_1y_avg: float          # mean of the rolling 20d vol over ~1y

    volume: float
    vol_avg_20: float          # 20-day average volume

    # Derived boolean flags (computed from the series in indicators):
    above_ma200: bool
    above_ma50: bool
    ma50_above_ma200: bool
    ma200_rising: bool
    golden_cross: bool         # MA50 crossed above MA200 today
    death_cross: bool          # MA50 crossed below MA200 today
    reclaimed_ma200: bool      # close crossed back above MA200 today
    broke_ma200: bool          # close crossed below MA200 today
    is_new_52w_high: bool

    # Layer-specific context filled by the runner (None where N/A):
    vix: float | None = None
    prev_vix: float | None = None


@dataclass
class DimensionScores:
    """The five-dimension breakdown behind T (architecture.md §3)."""

    direction: int             # 0–40
    momentum: int              # 0–30
    strength: int              # 0–15
    breadth: float             # 0–15 (market layer; inherited otherwise)


@dataclass
class TickerResult:
    """Per-ticker scoring output: trend score T and risk score R."""

    ticker: str
    name: str
    layer: Layer
    T: float                   # 0–100 trend score (油门)
    R: float                   # 0–100 risk score (刹车)
    dims: DimensionScores
    risk_flags: list[str]      # human-readable reasons R accumulated
    indicators: TickerIndicators


@dataclass
class RegimeState:
    """Confirmed regime for a ticker/layer, with change bookkeeping (§4.1)."""

    regime: Regime             # confirmed regime (after N-day confirmation)
    raw_regime: Regime         # today's unconfirmed classification
    prev_regime: Regime | None
    changed: bool              # confirmed regime differs from prior day
    reason: str                # why (references the triggering condition)


@dataclass
class Alert:
    """A discrete risk/opportunity event (architecture.md §5)."""

    kind: AlertKind
    ticker: str
    layer: Layer
    title: str
    detail: str
    severity: int              # higher = more important; used for ordering


@dataclass
class DailyReport:
    """Everything produced for one trading day. Persisted by snapshot."""

    date: str                  # ISO date (YYYY-MM-DD)
    market_regime: RegimeState  # the master switch (SPY-driven)
    breadth_pct: float         # fraction of sectors above 200MA
    vix: float | None
    results: dict[str, TickerResult] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)

    # ---- serialization (used by snapshot; keep round-trippable) ----------
    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "market_regime": _regime_state_to_dict(self.market_regime),
            "breadth_pct": self.breadth_pct,
            "vix": self.vix,
            "results": {t: _result_to_dict(r) for t, r in self.results.items()},
            "alerts": [_alert_to_dict(a) for a in self.alerts],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DailyReport":
        return cls(
            date=d["date"],
            market_regime=_regime_state_from_dict(d["market_regime"]),
            breadth_pct=d["breadth_pct"],
            vix=d.get("vix"),
            results={t: _result_from_dict(r) for t, r in d.get("results", {}).items()},
            alerts=[_alert_from_dict(a) for a in d.get("alerts", [])],
        )


# ---- (de)serialization helpers -------------------------------------------
def _regime_state_to_dict(s: RegimeState) -> dict:
    return {
        "regime": s.regime.value,
        "raw_regime": s.raw_regime.value,
        "prev_regime": s.prev_regime.value if s.prev_regime else None,
        "changed": s.changed,
        "reason": s.reason,
    }


def _regime_state_from_dict(d: dict) -> RegimeState:
    return RegimeState(
        regime=Regime(d["regime"]),
        raw_regime=Regime(d["raw_regime"]),
        prev_regime=Regime(d["prev_regime"]) if d.get("prev_regime") else None,
        changed=d["changed"],
        reason=d["reason"],
    )


def _indicators_to_dict(i: TickerIndicators) -> dict:
    d = asdict(i)
    d["layer"] = i.layer.value
    return d


def _indicators_from_dict(d: dict) -> TickerIndicators:
    d = dict(d)
    d["layer"] = Layer(d["layer"])
    return TickerIndicators(**d)


def _result_to_dict(r: TickerResult) -> dict:
    return {
        "ticker": r.ticker,
        "name": r.name,
        "layer": r.layer.value,
        "T": r.T,
        "R": r.R,
        "dims": asdict(r.dims),
        "risk_flags": list(r.risk_flags),
        "indicators": _indicators_to_dict(r.indicators),
    }


def _result_from_dict(d: dict) -> TickerResult:
    return TickerResult(
        ticker=d["ticker"],
        name=d["name"],
        layer=Layer(d["layer"]),
        T=d["T"],
        R=d["R"],
        dims=DimensionScores(**d["dims"]),
        risk_flags=list(d["risk_flags"]),
        indicators=_indicators_from_dict(d["indicators"]),
    )


def _alert_to_dict(a: Alert) -> dict:
    return {
        "kind": a.kind.value,
        "ticker": a.ticker,
        "layer": a.layer.value,
        "title": a.title,
        "detail": a.detail,
        "severity": a.severity,
    }


def _alert_from_dict(d: dict) -> Alert:
    return Alert(
        kind=AlertKind(d["kind"]),
        ticker=d["ticker"],
        layer=Layer(d["layer"]),
        title=d["title"],
        detail=d["detail"],
        severity=d["severity"],
    )
