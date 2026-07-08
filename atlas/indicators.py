"""Indicator computation (架构 §3).

Turns a raw OHLCV DataFrame into a fully-populated ``TickerIndicators``.
All technical indicators (ADX / MACD / RSI) are implemented by hand in
pandas/numpy — no pandas-ta or TA-Lib. Every field the scoring / regime /
alert layers rely on is filled here, guarded so downstream never sees NaN/inf.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .config import Layer
from .types import TickerIndicators


# --------------------------------------------------------------------------
# small numeric guards
# --------------------------------------------------------------------------
def _f(x, default: float = 0.0) -> float:
    """Coerce to a finite float; NaN/inf/None -> ``default``."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(v):
        return default
    return v


def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (RMA) = EWMA with alpha = 1/period."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def _safe_return(close: pd.Series, offset_from: int, offset_to: int) -> float:
    """Return close[-1-offset_from] / close[-1-offset_to] - 1, or 0 if short."""
    n = len(close)
    i_from = n - 1 - offset_from
    i_to = n - 1 - offset_to
    if i_from < 0 or i_to < 0:
        return 0.0
    base = close.iloc[i_to]
    if not np.isfinite(base) or base == 0:
        return 0.0
    return _f(close.iloc[i_from] / base - 1.0)


# --------------------------------------------------------------------------
# hand-rolled indicators
# --------------------------------------------------------------------------
def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Wilder ADX series."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    atr = _rma(tr, period)
    plus_di = 100.0 * _rma(plus_dm, period) / atr.replace(0, np.nan)
    minus_di = 100.0 * _rma(minus_dm, period) / atr.replace(0, np.nan)

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx = _rma(dx.fillna(0.0), period)
    return adx


def _macd_hist(close: pd.Series, fast: int, slow: int, signal: int) -> pd.Series:
    """MACD histogram series (DIF - DEA)."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif - dea


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder RSI series, clamped to [0, 100]."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _rma(gain, period)
    avg_loss = _rma(loss, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # where avg_loss == 0 -> all gains -> RSI 100
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi.clip(0.0, 100.0)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def compute_indicators(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    *,
    ticker: str,
    name: str,
    layer: Layer,
) -> TickerIndicators:
    """Compute all indicators for one ticker's OHLCV frame.

    ``df`` must have a DatetimeIndex (ascending) and columns
    Open/High/Low/Close/Volume. ``benchmark_df`` provides the relative-strength
    reference (typically SPY). Raises ValueError if too few bars.
    """
    min_bars = config.MA_LONG + config.MA_SLOPE_LOOKBACK
    if df is None or len(df) < min_bars:
        have = 0 if df is None else len(df)
        raise ValueError(
            f"{ticker}: 数据不足，需要至少 {min_bars} 根K线，仅有 {have} 根"
        )

    df = df.dropna(subset=["Close"])
    if len(df) < min_bars:
        raise ValueError(
            f"{ticker}: 有效收盘数据不足 {min_bars} 根（去除缺失后 {len(df)} 根）"
        )

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)

    close_val = _f(close.iloc[-1])
    prev_close = _f(close.iloc[-2])

    # --- moving averages ------------------------------------------------
    ma50_s = close.rolling(config.MA_SHORT).mean()
    ma200_s = close.rolling(config.MA_LONG).mean()
    ma50 = _f(ma50_s.iloc[-1], close_val)
    ma200 = _f(ma200_s.iloc[-1], close_val)
    prev_ma50 = _f(ma50_s.iloc[-2], ma50)
    prev_ma200 = _f(ma200_s.iloc[-2], ma200)
    ma200_prev = _f(ma200_s.iloc[-1 - config.MA_SLOPE_LOOKBACK], ma200)

    ma200_rising = ma200 > ma200_prev
    above_ma200 = close_val > ma200
    above_ma50 = close_val > ma50
    ma50_above_ma200 = ma50 > ma200

    golden_cross = (ma50 > ma200) and (prev_ma50 <= prev_ma200)
    death_cross = (ma50 < ma200) and (prev_ma50 >= prev_ma200)
    reclaimed_ma200 = (close_val > ma200) and (prev_close <= prev_ma200)
    broke_ma200 = (close_val < ma200) and (prev_close >= prev_ma200)

    # --- ADX / MACD / RSI ----------------------------------------------
    adx_s = _adx(high, low, close, config.ADX_PERIOD)
    adx = _f(adx_s.iloc[-1])

    macd_s = _macd_hist(close, config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    macd_hist = _f(macd_s.iloc[-1])

    rsi_s = _rsi(close, config.RSI_PERIOD)
    rsi = _f(rsi_s.iloc[-1], 50.0)
    prev_rsi = _f(rsi_s.iloc[-2], rsi)

    # --- momentum / returns --------------------------------------------
    skip = config.TRADING_DAYS_MONTH * config.MOM_SKIP_MONTHS
    lookback = config.TRADING_DAYS_MONTH * config.MOM_LOOKBACK_MONTHS
    mom_12_1 = _safe_return(close, offset_from=skip, offset_to=lookback)

    ret_6m = _safe_return(
        close, offset_from=0, offset_to=config.TRADING_DAYS_MONTH * config.RET_MID_MONTHS
    )

    # --- relative strength vs benchmark (3m) ---------------------------
    rs_window = config.TRADING_DAYS_MONTH * config.RS_MONTHS
    own_3m = _safe_return(close, offset_from=0, offset_to=rs_window)
    bench_3m = 0.0
    if benchmark_df is not None and "Close" in benchmark_df:
        bench_close = benchmark_df["Close"].astype(float).dropna()
        bench_3m = _safe_return(bench_close, offset_from=0, offset_to=rs_window)
    rs_3m = _f(own_3m - bench_3m)

    # --- 52-week high ---------------------------------------------------
    window_52w = config.TRADING_DAYS_YEAR
    high_52w = _f(close.tail(window_52w).max(), close_val)
    if high_52w <= 0:
        high_52w = close_val
    dist_to_52w_high = max(0.0, _f((high_52w - close_val) / high_52w)) if high_52w else 0.0
    is_new_52w_high = close_val >= high_52w * (1 - 1e-9)

    # --- drawdown -------------------------------------------------------
    peak = _f(close.tail(config.DRAWDOWN_LOOKBACK).max(), close_val)
    drawdown_60d = max(0.0, _f((peak - close_val) / peak)) if peak else 0.0

    # --- volatility -----------------------------------------------------
    daily_ret = close.pct_change()
    vol_20d = _f(daily_ret.tail(config.VOL_WINDOW).std())
    rolling_vol = daily_ret.rolling(config.VOL_WINDOW).std()
    vol_1y_avg = _f(rolling_vol.tail(config.TRADING_DAYS_YEAR).mean(), vol_20d)

    # --- volume ---------------------------------------------------------
    vol_latest = _f(volume.iloc[-1])
    vol_avg_20 = _f(volume.tail(config.VOL_WINDOW).mean(), vol_latest)

    return TickerIndicators(
        ticker=ticker,
        name=name,
        layer=layer,
        close=close_val,
        prev_close=prev_close,
        ma50=ma50,
        ma200=ma200,
        ma200_prev=ma200_prev,
        prev_ma50=prev_ma50,
        prev_ma200=prev_ma200,
        adx=adx,
        macd_hist=macd_hist,
        rsi=rsi,
        prev_rsi=prev_rsi,
        mom_12_1=mom_12_1,
        ret_6m=ret_6m,
        rs_3m=rs_3m,
        high_52w=high_52w,
        dist_to_52w_high=dist_to_52w_high,
        drawdown_60d=drawdown_60d,
        vol_20d=vol_20d,
        vol_1y_avg=vol_1y_avg,
        volume=vol_latest,
        vol_avg_20=vol_avg_20,
        above_ma200=bool(above_ma200),
        above_ma50=bool(above_ma50),
        ma50_above_ma200=bool(ma50_above_ma200),
        ma200_rising=bool(ma200_rising),
        golden_cross=bool(golden_cross),
        death_cross=bool(death_cross),
        reclaimed_ma200=bool(reclaimed_ma200),
        broke_ma200=bool(broke_ma200),
        is_new_52w_high=bool(is_new_52w_high),
        vix=None,
        prev_vix=None,
    )


def compute_breadth(sector_frames: dict[str, pd.DataFrame]) -> float:
    """Fraction of sector frames whose latest Close > that frame's MA200.

    Frames with fewer than MA_LONG bars are skipped. Returns 0.0 if no frame
    qualifies.
    """
    considered = 0
    above = 0
    for frame in (sector_frames or {}).values():
        if frame is None or "Close" not in frame:
            continue
        close = frame["Close"].astype(float).dropna()
        if len(close) < config.MA_LONG:
            continue
        ma200 = close.rolling(config.MA_LONG).mean().iloc[-1]
        latest = close.iloc[-1]
        if not (np.isfinite(ma200) and np.isfinite(latest)):
            continue
        considered += 1
        if latest > ma200:
            above += 1
    if considered == 0:
        return 0.0
    return above / considered
