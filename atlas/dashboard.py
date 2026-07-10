"""仪表盘渲染 (architecture.md §6).

把一天的 :class:`DailyReport` 渲染成 **一页自包含 HTML**（内联 CSS，无外部资源，
可离线打开）。布局自上而下：制度灯 → 大盘卡片 → 行业热力图 → 多资产条 →
自选个股表 → 今日提示。

配色遵循数据可视化规范：绿=机会/强势、红=风险/弱势、黄=警戒；始终「颜色 + 图标/
文字」成对出现，避免仅靠红绿区分（对色盲不友好）。所有展示逻辑在此模块算好，模板
只做排版。
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config
from .config import Layer
from .types import (
    REGIME_LABEL,
    REGIME_LIGHT,
    REGIME_STANCE,
    AlertKind,
    DailyReport,
    Regime,
    TickerResult,
)

_TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"
_TEMPLATE_NAME = "dashboard.html.j2"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _fmt_num(x: float) -> str:
    """整数不带小数，否则保留一位。"""
    xf = float(x)
    return f"{xf:.0f}" if xf.is_integer() else f"{xf:.1f}"


def _fmt_pct(x: float, *, signed: bool = False, digits: int = 1) -> str:
    """把小数 (0.05) 格式化成百分比字符串 ('5.0%')。"""
    v = x * 100.0
    if signed:
        return f"{v:+.{digits}f}%"
    return f"{v:.{digits}f}%"


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _fmt_price(x: float) -> str:
    """价格：两位小数、千分位分隔。"""
    return f"{float(x):,.2f}"


def _day_change(result: TickerResult) -> float:
    """当日涨跌幅（最新收盘 vs 昨收），小数。"""
    ind = result.indicators
    if ind.prev_close:
        return ind.close / ind.prev_close - 1.0
    return 0.0


def _price_fields(result: TickerResult) -> dict:
    """现价 + 当日涨跌幅，供各板块统一复用。"""
    chg = _day_change(result)
    return {
        "price": _fmt_price(result.indicators.close),
        "chg": _fmt_pct(chg, signed=True),
        "chg_good": chg >= 0,
    }


# --------------------------------------------------------------------------
# 颜色 / 状态映射
# --------------------------------------------------------------------------
# 热力图色带：低分红 → 中分琥珀 → 高分绿。同一色系深浅表达强弱，
# 并始终配合 T 数值与中文名，绝不单靠颜色。
_HEAT_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.0, (198, 55, 55)),     # 深红：弱势破位
    (35.0, (214, 110, 55)),   # 橙
    (50.0, (222, 178, 60)),   # 琥珀
    (65.0, (140, 180, 70)),   # 黄绿
    (100.0, (46, 150, 88)),   # 绿：强势领先
]


def _heat_rgb(t: float) -> tuple[int, int, int]:
    t = _clamp(float(t), 0.0, 100.0)
    for (x0, c0), (x1, c1) in zip(_HEAT_STOPS, _HEAT_STOPS[1:]):
        if t <= x1:
            span = x1 - x0
            frac = 0.0 if span == 0 else (t - x0) / span
            return tuple(round(a + (b - a) * frac) for a, b in zip(c0, c1))  # type: ignore[return-value]
    return _HEAT_STOPS[-1][1]


def _text_on(rgb: tuple[int, int, int]) -> str:
    """按背景亮度选深/浅字，保证对比度。"""
    r, g, b = rgb
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#14181f" if lum > 0.6 else "#ffffff"


def _heat_style(t: float) -> str:
    rgb = _heat_rgb(t)
    return f"background:rgb({rgb[0]},{rgb[1]},{rgb[2]});color:{_text_on(rgb)};"


def _status_tag(result: TickerResult) -> dict:
    """大盘/通用状态标签：强势 / 警戒 / 弱势（含图标，防御优先）。"""
    T, R = result.T, result.R
    if R >= config.R_HIGH or T <= config.T_WEAK:
        return {"label": "弱势防御", "icon": "🔴", "cls": "tag-bad"}
    if T >= config.T_STRONG and R <= config.R_LOW:
        return {"label": "强势", "icon": "🟢", "cls": "tag-good"}
    return {"label": "警戒", "icon": "🟡", "cls": "tag-warn"}


def _risk_flag(R: float) -> dict:
    """个股风险旗标：R 越高越危险（🔴/🟡/🟢）。"""
    if R >= config.R_HIGH:
        return {"label": "高风险", "icon": "🔴", "cls": "tag-bad"}
    if R >= config.R_LOW:
        return {"label": "中风险", "icon": "🟡", "cls": "tag-warn"}
    return {"label": "低风险", "icon": "🟢", "cls": "tag-good"}


def _trend_status(result: TickerResult) -> dict:
    """趋势状态：多头 / 震荡 / 空头破位（图标 + 中文）。"""
    ind = result.indicators
    if not ind.above_ma200:
        return {"label": "空头破位", "icon": "▼", "cls": "tag-bad"}
    if ind.above_ma50 and ind.ma50_above_ma200:
        return {"label": "多头", "icon": "▲", "cls": "tag-good"}
    return {"label": "震荡", "icon": "◆", "cls": "tag-warn"}


def _arrow(result: TickerResult) -> dict:
    """多资产趋势箭头：站上 50 日线 = 上行，反之下行。"""
    if result.indicators.above_ma50:
        return {"icon": "↑", "label": "上行", "cls": "tag-good"}
    return {"icon": "↓", "label": "下行", "cls": "tag-bad"}


def _dist_to_ma200(result: TickerResult) -> float:
    ind = result.indicators
    if ind.ma200 <= 0:
        return 0.0
    return (ind.close - ind.ma200) / ind.ma200


# --------------------------------------------------------------------------
# 视图模型
# --------------------------------------------------------------------------
def _by_layer(report: DailyReport, layer: Layer) -> list[TickerResult]:
    return [r for r in report.results.values() if r.layer == layer]


def _market_view(report: DailyReport) -> list[dict]:
    order = list(config.MARKET_TICKERS)
    rows = _by_layer(report, Layer.MARKET)
    rows.sort(key=lambda r: order.index(r.ticker) if r.ticker in order else 999)
    out = []
    for r in rows:
        d = _dist_to_ma200(r)
        out.append({
            "ticker": r.ticker,
            "name": r.name,
            "T": _fmt_num(r.T),
            "R": _fmt_num(r.R),
            "status": _status_tag(r),
            "dist200": _fmt_pct(d, signed=True),
            "dist200_good": d >= 0,
            **_price_fields(r),
        })
    return out


def _sector_view(report: DailyReport) -> list[dict]:
    rows = _by_layer(report, Layer.SECTOR)
    rows.sort(key=lambda r: r.T, reverse=True)  # 强 → 弱
    return [{
        "ticker": r.ticker,
        "name": r.name,
        "T": _fmt_num(r.T),
        "style": _heat_style(r.T),
        "status": _status_tag(r),
        **_price_fields(r),
    } for r in rows]


def _multi_asset_view(report: DailyReport) -> list[dict]:
    order = list(config.MULTI_ASSET_TICKERS)
    rows = _by_layer(report, Layer.MULTI_ASSET)
    rows.sort(key=lambda r: order.index(r.ticker) if r.ticker in order else 999)
    return [{
        "ticker": r.ticker,
        "name": r.name,
        "T": _fmt_num(r.T),
        "arrow": _arrow(r),
        **_price_fields(r),
    } for r in rows]


def _stock_view(report: DailyReport, detail_links: dict[str, str] | None = None) -> list[dict]:
    links = detail_links or {}
    rows = _by_layer(report, Layer.STOCK)
    out = []
    for r in rows:
        ind = r.indicators
        hints = [a for a in report.alerts if a.ticker == r.ticker]
        hints.sort(key=lambda a: a.severity, reverse=True)
        out.append({
            "ticker": r.ticker,
            "name": r.name,
            "link": links.get(r.ticker),
            **_price_fields(r),
            "tone": _status_tag(r)["cls"].removeprefix("tag-"),  # good/warn/bad 行着色
            "trend": _trend_status(r),
            "mom": _fmt_pct(ind.mom_12_1, signed=True),
            "mom_good": ind.mom_12_1 >= 0,
            "dist_high": _fmt_pct(-ind.dist_to_52w_high, signed=True),
            "risk": _risk_flag(r.R),
            "hints": [{
                "title": a.title,
                "is_risk": a.kind == AlertKind.RISK,
            } for a in hints],
        })
    return out


def _alert_view(report: DailyReport) -> list[dict]:
    alerts = sorted(report.alerts, key=lambda a: a.severity, reverse=True)
    out = []
    for a in alerts:
        is_risk = a.kind == AlertKind.RISK
        out.append({
            "is_risk": is_risk,
            "icon": "⚠️" if is_risk else "✨",
            "kind_label": "风险" if is_risk else "机会",
            "ticker": a.ticker,
            "name": config.name_of(a.ticker),
            "title": a.title,
            "detail": a.detail,
            "severity": a.severity,
        })
    return out


def _regime_view(report: DailyReport) -> dict:
    st = report.market_regime
    reg = st.regime
    view = {
        "light": REGIME_LIGHT[reg],
        "label": REGIME_LABEL[reg],
        "stance": REGIME_STANCE[reg],
        "reason": st.reason,
        "changed": st.changed,
        "cls": {
            Regime.RISK_ON: "regime-on",
            Regime.CAUTION: "regime-caution",
            Regime.RISK_OFF: "regime-off",
            Regime.OVERSOLD: "regime-oversold",
        }[reg],
        "prev": None,
    }
    if st.changed and st.prev_regime is not None:
        view["prev"] = {
            "light": REGIME_LIGHT[st.prev_regime],
            "label": REGIME_LABEL[st.prev_regime],
        }
    return view


def _build_context(
    report: DailyReport,
    prev_report: DailyReport | None,
    source: str | None,
    generated_at: str | None,
    detail_links: dict[str, str] | None,
    explain: dict | None,
    state: dict | None,
) -> dict:
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    prev_regime_label = None
    if state and state.get("previous_regime"):
        prev_regime_label = REGIME_LABEL[Regime(state["previous_regime"])]
    return {
        "date": report.date,
        "prev_date": prev_report.date if prev_report else None,
        "source": source or "yfinance",
        "generated_at": generated_at,
        "regime": _regime_view(report),
        "state": state,
        "prev_regime_label": prev_regime_label,
        "explain": explain,
        "breadth_pct": _fmt_pct(report.breadth_pct),
        "vix": _fmt_num(report.vix) if report.vix is not None else "—",
        "markets": _market_view(report),
        "sectors": _sector_view(report),
        "multi_assets": _multi_asset_view(report),
        "stocks": _stock_view(report, detail_links),
        "alerts": _alert_view(report),
    }


# --------------------------------------------------------------------------
# 公共 API
# --------------------------------------------------------------------------
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_dashboard(
    report: DailyReport,
    prev_report: DailyReport | None,
    *,
    source: str | None = None,
    generated_at: str | None = None,
    detail_links: dict[str, str] | None = None,
    explain: dict | None = None,
    state: dict | None = None,
) -> str:
    """渲染完整的自包含 HTML 字符串。source 为数据来源标注，detail_links 为个股详情页链接，
    explain 为解释层摘要、state 为制度持续状态（均可选，缺省时看板不显示对应块）。"""
    env = _environment()
    template = env.get_template(_TEMPLATE_NAME)
    return template.render(
        **_build_context(report, prev_report, source, generated_at, detail_links, explain, state)
    )


def write_dashboard(
    report: DailyReport,
    prev_report: DailyReport | None,
    path: str = config.DEFAULT_OUTPUT,
    *,
    source: str | None = None,
    generated_at: str | None = None,
    detail_links: dict[str, str] | None = None,
    explain: dict | None = None,
    state: dict | None = None,
) -> None:
    """渲染并写入 ``path``（UTF-8）。若父目录不存在则自动创建。"""
    html = render_dashboard(
        report, prev_report, source=source, generated_at=generated_at,
        detail_links=detail_links, explain=explain, state=state,
    )
    parent = pathlib.Path(path).resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
