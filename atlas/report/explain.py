"""解释层：把一天的 DailyReport 组织成面向人的「每日报告」摘要。

输出四块（进 ``latest.json.explain``，并喂给看板顶部）：

  * ``headline``               一句话结论（暴露语言：持有 / 收敛 / 降敞口 / 观察）。
  * ``top_risks``              最重要的风险（取自已按 severity 排序的 alerts）。
  * ``top_opportunities``      最重要的机会（同上）。
  * ``delta_from_yesterday``   较昨日的变化（制度切换 + 新增 alert），只讲变化。

**纪律（铁律）**：
  Ⅰ 生存优先 —— 风险块权重高于机会块，headline 由防御优先的制度直接决定。
  Ⅱ 不预测，只响应 —— 只描述已发生的状态，**绝不**输出买卖 / 涨跌 / 价格目标 /
    未来收益。全部为暴露 / 状态语言。
  可溯源 —— 每条 risk/opportunity 都对应一条 alert；headline/delta 对应制度规则。

本模块是**纯派生**：不新增指标、不读数据源，只消费 DailyReport 与前一日报告。
"""
from __future__ import annotations

from .. import config
from ..types import AlertKind, DailyReport, Regime

# 制度 → 一句话结论。措辞全部为「暴露 / 应对」语言，不含任何方向预测。
# 防御优先：Risk-Off 永远是「降敞口」，不因个别强势标的而软化。
_HEADLINE: dict[Regime, str] = {
    Regime.RISK_ON: "趋势健康，顺势持有",
    Regime.CAUTION: "趋势仍在，但风险升高，收紧仓位",
    Regime.RISK_OFF: "防御优先，降低风险敞口",
    Regime.OVERSOLD: "超卖观察，不急于抄底",
}

# 伪 ticker（非真实标的）：这些 alert 标题本身自足，无需再前缀中文名。
_PSEUDO_TICKERS = frozenset({"BREADTH"})

_MAX_ALERTS = 3      # top_risks / top_opportunities 各取前 N（不对称：风险先列）
_MAX_DELTA = 5       # 较昨日变化最多列 N 条


def _regime_label(regime: Regime) -> str:
    from ..types import REGIME_LABEL
    return REGIME_LABEL[regime]


def _alert_label(ticker: str, title: str) -> str:
    """把一条 alert 变成一行人话：`中文名：标题`；伪 ticker 只用标题。"""
    if ticker in _PSEUDO_TICKERS:
        return title
    name = config.name_of(ticker)
    if not name or name == ticker:
        return f"{ticker}：{title}"
    return f"{name}：{title}"


def _top(report: DailyReport, kind: AlertKind, limit: int) -> list[str]:
    """某一类 alert 的前 ``limit`` 条标签，按 severity 降序（同分保持稳定顺序）。"""
    picked = sorted(
        (a for a in report.alerts if a.kind == kind),
        key=lambda a: a.severity,
        reverse=True,
    )
    labels: list[str] = []
    for a in picked:
        label = _alert_label(a.ticker, a.title)
        if label not in labels:            # 同一事件不重复列
            labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _delta(report: DailyReport, prev: DailyReport | None, limit: int) -> list[str]:
    """较昨日的变化：制度切换（最高优先） + 今日新增的 alert。"""
    if prev is None:
        return []

    out: list[str] = []

    # 1) 制度切换（master switch，永远排第一）。
    st = report.market_regime
    if st.changed and st.prev_regime is not None:
        out.append(
            f"市场制度：{_regime_label(st.prev_regime)} → {_regime_label(st.regime)}"
        )

    # 2) 今日新增的 alert（昨日不存在的 (ticker, title)）。风险优先。
    prev_keys = {(a.ticker, a.title) for a in prev.alerts}
    fresh = [a for a in report.alerts if (a.ticker, a.title) not in prev_keys]
    fresh.sort(key=lambda a: (a.kind != AlertKind.RISK, -a.severity))
    for a in fresh:
        tag = "新增风险" if a.kind == AlertKind.RISK else "新增机会"
        line = f"{tag}：{_alert_label(a.ticker, a.title)}"
        if line not in out:
            out.append(line)
        if len(out) >= limit:
            break
    return out[:limit]


def build_explain(report: DailyReport, prev_report: DailyReport | None) -> dict:
    """组装解释层摘要。纯派生，无副作用。"""
    return {
        "headline": _HEADLINE[report.market_regime.regime],
        "top_risks": _top(report, AlertKind.RISK, _MAX_ALERTS),
        "top_opportunities": _top(report, AlertKind.OPPORTUNITY, _MAX_ALERTS),
        "delta_from_yesterday": _delta(report, prev_report, _MAX_DELTA),
    }
