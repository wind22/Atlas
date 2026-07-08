"""Alerts: discrete risk / opportunity events (architecture.md §5).

Alerts complement the daily regime light. Per 铁律 Ⅲ (不对称凸性), risk alerts
are more sensitive and carry higher severity than opportunity alerts of the same
family — cutting losses early beats entering early. Severity is an int where
higher == more important; callers sort descending.

Nothing here is tuned: every threshold traces to atlas.config.
"""
from __future__ import annotations

from . import config
from .config import Layer
from .types import Alert, AlertKind, TickerIndicators, TickerResult


def detect_alerts(
    result: TickerResult, prev_indicators: TickerIndicators | None
) -> list[Alert]:
    """Scan one ticker for §5.1 risk and §5.2 opportunity events."""
    ind = result.indicators
    ticker = result.ticker
    layer = result.layer
    alerts: list[Alert] = []

    # ---- 风险类 (§5.1) ---------------------------------------------------
    if ind.broke_ma200:
        alerts.append(Alert(
            kind=AlertKind.RISK, ticker=ticker, layer=layer,
            title="跌破200日均线",
            detail=f"收盘{ind.close:.2f} 由上转下跌破200日均线{ind.ma200:.2f}，最重要的风险信号",
            severity=10,
        ))

    if ind.death_cross:
        alerts.append(Alert(
            kind=AlertKind.RISK, ticker=ticker, layer=layer,
            title="死叉：50日下穿200日",
            detail=f"50日均线{ind.ma50:.2f}下穿200日均线{ind.ma200:.2f}，中期趋势转弱确认",
            severity=8,
        ))

    if ind.vix is not None:
        panic = ind.vix > config.VIX_PANIC
        jump = ind.prev_vix is not None and ind.prev_vix > 0 and (
            ind.vix / ind.prev_vix - 1.0 > config.VIX_JUMP_PCT
        )
        if panic or jump:
            if jump and ind.prev_vix:
                pct = (ind.vix / ind.prev_vix - 1.0) * 100.0
                detail = f"VIX={ind.vix:.1f}，单日跳升{pct:.0f}%，市场恐慌、波动风险骤升"
            else:
                detail = f"VIX={ind.vix:.1f}，突破恐慌阈值{config.VIX_PANIC:.0f}，波动风险骤升"
            alerts.append(Alert(
                kind=AlertKind.RISK, ticker=ticker, layer=layer,
                title="VIX恐慌跳升", detail=detail, severity=9,
            ))

    if ind.vol_1y_avg > 0 and ind.vol_20d > ind.vol_1y_avg * config.VOL_SPIKE_MULT:
        alerts.append(Alert(
            kind=AlertKind.RISK, ticker=ticker, layer=layer,
            title="波动率突破近一年上沿",
            detail=(f"20日波动率{ind.vol_20d:.3f} > 近一年均值{ind.vol_1y_avg:.3f}×"
                    f"{config.VOL_SPIKE_MULT}，不确定性上升，仓位应收敛"),
            severity=5,
        ))

    if ind.drawdown_60d > config.DRAWDOWN_SEVERE:
        alerts.append(Alert(
            kind=AlertKind.RISK, ticker=ticker, layer=layer,
            title="深度回撤>20%",
            detail=f"较60日高点回撤{ind.drawdown_60d * 100:.1f}%，深度回撤、风险偏高",
            severity=6,
        ))

    # ---- 机会类 (§5.2) ---------------------------------------------------
    if ind.reclaimed_ma200:
        alerts.append(Alert(
            kind=AlertKind.OPPORTUNITY, ticker=ticker, layer=layer,
            title="重新站上200日均线",
            detail=f"收盘{ind.close:.2f} 由下转上收复200日均线{ind.ma200:.2f}，制度或转向进攻",
            severity=9,  # strictly below broke_ma200 (10): risk outranks its opp. counterpart
        ))

    if ind.golden_cross:
        alerts.append(Alert(
            kind=AlertKind.OPPORTUNITY, ticker=ticker, layer=layer,
            title="金叉：50日上穿200日",
            detail=f"50日均线{ind.ma50:.2f}上穿200日均线{ind.ma200:.2f}，中期趋势转多确认",
            severity=7,  # strictly below death_cross (8): risk outranks its opp. counterpart
        ))

    if ind.is_new_52w_high and ind.vol_avg_20 > 0 and (
        ind.volume > ind.vol_avg_20 * config.VOLUME_BREAKOUT_MULT
    ):
        alerts.append(Alert(
            kind=AlertKind.OPPORTUNITY, ticker=ticker, layer=layer,
            title="放量创52周新高",
            detail=(f"创52周新高{ind.high_52w:.2f}，成交量{ind.volume:.0f} > 20日均量×"
                    f"{config.VOLUME_BREAKOUT_MULT}，放量突破、趋势启动"),
            severity=7,
        ))

    # 深度回撤后企稳：重回200线 + RSI 从超卖回升 (best-effort, needs prev)
    if (prev_indicators is not None and ind.reclaimed_ma200
            and prev_indicators.rsi < config.RSI_OVERSOLD
            and ind.rsi > prev_indicators.rsi):
        alerts.append(Alert(
            kind=AlertKind.OPPORTUNITY, ticker=ticker, layer=layer,
            title="超卖修复企稳",
            detail=(f"重回200日均线，RSI由{prev_indicators.rsi:.0f}回升至{ind.rsi:.0f}，"
                    "超卖修复、趋势可能重建"),
            severity=6,
        ))

    return alerts


def detect_breadth_alert(
    breadth_pct: float, prev_breadth_pct: float | None
) -> Alert | None:
    """§5.1 广度骤降：>60% 一周内跌至 <40% → 普涨转分化."""
    if (prev_breadth_pct is not None
            and prev_breadth_pct >= config.BREADTH_FULL
            and breadth_pct < config.BREADTH_WEAK):
        return Alert(
            kind=AlertKind.RISK, ticker="BREADTH", layer=Layer.MARKET,
            title="广度骤降（普涨转分化）",
            detail=(f"200线上方行业占比由{prev_breadth_pct * 100:.0f}%骤降至"
                    f"{breadth_pct * 100:.0f}%，市场内部转弱、普涨变分化"),
            severity=7,
        )
    return None


def detect_multi_asset_alerts(results: dict[str, TickerResult]) -> list[Alert]:
    """§5.2 多资产切换 Risk-On：股↑ 同时 债↓ 金↓ (above_ma50 as trend proxy)."""
    alerts: list[Alert] = []

    equity = None
    for sym in ("SPY", "QQQ"):
        r = results.get(sym)
        if r is not None and r.indicators.above_ma50:
            equity = sym
            break

    tlt = results.get("TLT")
    gld = results.get("GLD")
    if equity is None or tlt is None or gld is None:
        return alerts

    if not tlt.indicators.above_ma50 and not gld.indicators.above_ma50:
        alerts.append(Alert(
            kind=AlertKind.OPPORTUNITY, ticker=equity, layer=Layer.MULTI_ASSET,
            title="多资产切换 Risk-On（股↑债↓金↓）",
            detail=(f"{equity}在50日均线上方而长债TLT、黄金GLD同步走弱，"
                    "市场风险偏好回升"),
            severity=6,
        ))

    return alerts
