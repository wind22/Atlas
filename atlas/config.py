"""Central configuration: universe, parameters, thresholds, weights.

Every constant here is a fixed industry-standard value. Per 铁律 Ⅱ (谦逊 /
anti-overfitting), these are NEVER tuned to historical data. Changing a value
here changes system behavior globally and must be a deliberate design decision,
not a backtest optimization.

All numbers trace directly to architecture.md §3 (scoring) and §4 (regime).
"""
from __future__ import annotations

from enum import Enum


class Layer(str, Enum):
    """The four monitoring layers (监控范围, architecture.md §2)."""

    MARKET = "market"            # ① 大盘层 — overall regime, the master switch
    SECTOR = "sector"            # ② 行业层 — sector rotation / relative strength
    MULTI_ASSET = "multi_asset"  # ③ 多资产层 — risk-on/off barometer
    STOCK = "stock"              # ④ 自选个股层 — user watchlist


# --------------------------------------------------------------------------
# Universe (架构 §2). Maps ticker -> Chinese display name.
# --------------------------------------------------------------------------
MARKET_TICKERS: dict[str, str] = {
    "SPY": "标普500",
    "QQQ": "纳指100",
    "IWM": "罗素2000",
    "DIA": "道指",
}

SECTOR_TICKERS: dict[str, str] = {
    "XLK": "科技",
    "XLF": "金融",
    "XLE": "能源",
    "XLV": "医疗",
    "XLI": "工业",
    "XLY": "可选消费",
    "XLP": "必需消费",
    "XLU": "公用",
    "XLB": "材料",
    "XLRE": "地产",
    "XLC": "通信",
}

MULTI_ASSET_TICKERS: dict[str, str] = {
    "TLT": "长债",
    "IEF": "中债",
    "GLD": "黄金",
    "DBC": "商品",
    "UUP": "美元",
    "HYG": "高收益债",
}

DEFAULT_STOCKS: dict[str, str] = {
    "AAPL": "苹果",
    "MSFT": "微软",
    "NVDA": "英伟达",
    "GOOG": "谷歌",
    "META": "Meta",
    "AMD": "AMD",
    "TSLA": "特斯拉",
    "PDD": "拼多多",
    "CLSK": "CleanSpark",
    "MU": "美光",             # 存储芯片
    "1810.HK": "小米集团",     # 港股
    "159605.SZ": "159605",     # 深证 ETF
    "005930.KS": "三星电子",   # 韩股
    "000660.KS": "SK海力士",   # 韩股
    "562590.SS": "半导体设备ETF",  # 沪市 ETF（华夏中证半导体材料设备主题）
    "^N225": "日经225",        # 日本大盘指数（跨市场，作观察标的纳入）
}
# 注：港股 / A股 / 韩股 / 日经的「相对 SPY 强度」为跨市场近似口径，仅供参考。
# 日经 ^N225 本为指数（非个股），此处作为观察 / 回测标的纳入自选层。
# NASDAQ 为指数（已由市场层 QQQ 代表），未纳入个股表；如需可加 "^IXIC"。

BENCHMARK: str = "SPY"        # relative-strength benchmark
VIX_TICKER: str = "^VIX"      # market-fear gauge


def layer_of(ticker: str) -> Layer:
    """Return the layer a universe ticker belongs to (STOCK if unknown)."""
    if ticker in MARKET_TICKERS:
        return Layer.MARKET
    if ticker in SECTOR_TICKERS:
        return Layer.SECTOR
    if ticker in MULTI_ASSET_TICKERS:
        return Layer.MULTI_ASSET
    return Layer.STOCK


def name_of(ticker: str, stocks: dict[str, str] | None = None) -> str:
    """Chinese display name for a ticker, falling back to the ticker itself."""
    for table in (MARKET_TICKERS, SECTOR_TICKERS, MULTI_ASSET_TICKERS, stocks or DEFAULT_STOCKS):
        if ticker in table:
            return table[ticker]
    return ticker


# --------------------------------------------------------------------------
# Indicator parameters (架构 §3). Fixed industry-standard values.
# --------------------------------------------------------------------------
MA_LONG = 200                 # 长期牛熊线
MA_SHORT = 50                 # 中期均线
MA_SLOPE_LOOKBACK = 20        # 200MA slope: today vs 20 trading days ago
ADX_PERIOD = 14
ADX_TREND = 25                # ADX >= 25 → a real trend, not chop
RSI_PERIOD = 14
RSI_OVERSOLD = 30             # RSI < 30 → oversold
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

TRADING_DAYS_MONTH = 21       # approx trading days per calendar month
TRADING_DAYS_YEAR = 252

MOM_LOOKBACK_MONTHS = 12      # 12-1 momentum: skip the most recent month
MOM_SKIP_MONTHS = 1
RET_MID_MONTHS = 6           # 6-month return
RS_MONTHS = 3                # relative strength lookback vs benchmark
NEAR_HIGH_PCT = 0.05         # within 5% of 52-week high == strong
WEEKS_52 = 52

DRAWDOWN_LOOKBACK = 60       # drawdown measured from the 60-day peak
DRAWDOWN_WARN = 0.10         # > 10% drawdown → +risk
DRAWDOWN_SEVERE = 0.20       # > 20% drawdown → extra risk

VOL_WINDOW = 20             # realized-vol window (20d)
VOL_SPIKE_MULT = 1.5        # 20d vol > 1.5x its 1-year average → spike

VIX_ELEVATED = 20.0
VIX_PANIC = 30.0
VIX_JUMP_PCT = 0.50         # VIX single-day jump > 50% → alert

# Breadth: fraction of sector ETFs trading above their 200-day MA.
BREADTH_FULL = 0.60         # >= 60% → full breadth score (15)
BREADTH_ZERO = 0.20         # <= 20% → breadth score ~0
BREADTH_WEAK = 0.40         # < 40% → contributes to risk

VOLUME_BREAKOUT_MULT = 1.5  # breakout volume > 1.5x 20-day average volume

# --------------------------------------------------------------------------
# Trend score T weights (架构 §3, T = 100). Sub-weights sum to their column.
# --------------------------------------------------------------------------
W_DIRECTION = 40
W_MOMENTUM = 30
W_STRENGTH = 15
W_BREADTH = 15

# Direction (0–40)
DIR_ABOVE_MA200 = 16
DIR_ABOVE_MA50 = 8
DIR_MA50_ABOVE_MA200 = 8
DIR_MA200_RISING = 8

# Momentum (0–30)
MOM_12_1_POS = 10
MOM_6M_POS = 6
MOM_RS_POS = 8
MOM_NEAR_HIGH = 6

# Strength (0–15)
STR_ADX = 8
STR_MACD = 7

# --------------------------------------------------------------------------
# Risk score R additions (架构 §3.5, cap 100). Any condition true → add.
# --------------------------------------------------------------------------
RISK_BELOW_MA200 = 25
RISK_DEATH_CROSS = 15
RISK_DRAWDOWN = 15           # drawdown > 10%
RISK_DRAWDOWN_SEVERE_EXTRA = 10   # drawdown > 20% → extra
RISK_VOL_SPIKE = 20
RISK_VIX = 10               # VIX > 20
RISK_VIX_PANIC_EXTRA = 10   # VIX > 30 → extra
RISK_BREADTH_WEAK = 15
RISK_CAP = 100

# --------------------------------------------------------------------------
# Regime bands (架构 §4).
# --------------------------------------------------------------------------
T_STRONG = 60               # T >= 60 (with low R) → Risk-On
T_WEAK = 35                 # T <= 35 → Risk-Off
R_LOW = 30                  # R <= 30 required for Risk-On
R_HIGH = 60                 # R >= 60 → Risk-Off (defense outranks offense)
REGIME_CONFIRM_DAYS = 2     # a switch confirms only after N consecutive days

# --------------------------------------------------------------------------
# Data / runtime defaults.
# --------------------------------------------------------------------------
DEFAULT_PERIOD = "2y"       # yfinance history window (need >1y for MA200 + vol)
DEFAULT_DB = "atlas_snapshots.sqlite"
DEFAULT_OUTPUT = "dashboard.html"
