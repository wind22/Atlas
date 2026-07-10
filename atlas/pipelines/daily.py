"""每日流水线编排 (架构 §2 数据流全链路 / 方案 §5).

``run()`` 把每一层串起来跑一个交易日：
  data_fetch → indicators → scoring → regime + alerts → DailyReport
  → 报告层(explain/state/similarity) → storage(snapshot + 数据产物)
  → site(dashboard/detail/about 渲染)。

单只标的出错绝不中断整轮——每标的的计算都被包起来，报告优雅降级。CLI 入口在
:mod:`atlas.runner`，与本编排层分离。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pandas as pd

from .. import config
from .. import data_fetch, indicators, scoring, alerts
from .. import regime as regime_mod
from ..storage import artifacts
from ..storage import snapshot_store as snapshot
from ..report import explain as explain_mod
from ..report import state_machine
from ..report import similarity
from ..site import dashboard, detail, about
from ..config import Layer
from ..types import (
    Alert,
    DailyReport,
    Regime,
    RegimeState,
    TickerIndicators,
    TickerResult,
)

_VIX_LAYERS = (Layer.MARKET, Layer.MULTI_ASSET)


def _warn(msg: str) -> None:
    print(f"[atlas] {msg}", file=sys.stderr)


def _unique(seq: list[str]) -> list[str]:
    """Order-preserving de-duplication."""
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _latest_two_close(frame: pd.DataFrame | None) -> tuple[float | None, float | None]:
    """(latest_close, prev_close) from a frame's Close, or (None, None)."""
    if frame is None or "Close" not in frame:
        return None, None
    close = frame["Close"].astype(float).dropna()
    if len(close) == 0:
        return None, None
    latest = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) >= 2 else None
    return latest, prev


def run(
    *,
    stocks: dict[str, str] | None = None,
    offline: bool = False,
    period: str = config.DEFAULT_PERIOD,
    db_path: str = config.DEFAULT_DB,
    output: str = config.DEFAULT_OUTPUT,
    as_of: str | None = None,
    write_details: bool = True,
) -> DailyReport:
    """Run the full monitor pipeline and return the assembled DailyReport."""
    stocks = stocks or dict(config.DEFAULT_STOCKS)

    # 1) Universe -----------------------------------------------------------
    universe = _unique(
        list(config.MARKET_TICKERS)
        + list(config.SECTOR_TICKERS)
        + list(config.MULTI_ASSET_TICKERS)
        + list(stocks)
        + [config.BENCHMARK]
    )
    all_tickers = _unique(universe + [config.VIX_TICKER])

    # 2) Fetch --------------------------------------------------------------
    if offline:
        frames = data_fetch.synthetic_prices(all_tickers)
        vix_frame = frames.get(config.VIX_TICKER)
    else:
        frames = data_fetch.fetch_prices(universe, period=period)
        vix_frame = data_fetch.fetch_vix(period=period)

    benchmark_df = frames.get(config.BENCHMARK)
    if benchmark_df is None or "Close" not in benchmark_df or len(benchmark_df) == 0:
        raise RuntimeError(
            f"基准 {config.BENCHMARK} 数据缺失，无法运行（请检查数据源或使用 --offline）"
        )

    # 3) as_of date = benchmark's last bar unless overridden ----------------
    if as_of is None:
        as_of = pd.Timestamp(benchmark_df.index[-1]).date().isoformat()

    # 4) breadth + VIX context ---------------------------------------------
    sector_frames = {
        t: frames[t] for t in config.SECTOR_TICKERS if t in frames
    }
    breadth = indicators.compute_breadth(sector_frames)
    vix_latest, vix_prev = _latest_two_close(vix_frame)

    # 5) Per-ticker indicators + scoring -----------------------------------
    results: dict[str, TickerResult] = {}
    for ticker in universe:
        df = frames.get(ticker)
        if df is None:
            _warn(f"{ticker}: 无数据，跳过")
            continue
        try:
            layer = config.layer_of(ticker)
            name = config.name_of(ticker, stocks)
            ind = indicators.compute_indicators(
                df, benchmark_df, ticker=ticker, name=name, layer=layer
            )
            if layer in _VIX_LAYERS:
                ind.vix = vix_latest
                ind.prev_vix = vix_prev
            layer_vix = vix_latest if layer in _VIX_LAYERS else None
            results[ticker] = scoring.score_ticker(
                ind, breadth_pct=breadth, vix=layer_vix
            )
        except Exception as exc:  # noqa: BLE001 — one bad ticker never aborts
            _warn(f"{ticker}: 计算失败（{exc!r}），跳过")
            continue

    # 6) Market regime (SPY-driven) ----------------------------------------
    prev_report = snapshot.load_previous(as_of, db_path)
    spy_result = results.get(config.BENCHMARK)
    if spy_result is None:
        raise RuntimeError(f"{config.BENCHMARK} 计算失败，无法判定市场制度")

    today_raw, _ = regime_mod.classify(spy_result)
    older = snapshot.load_recent(as_of, config.REGIME_CONFIRM_DAYS - 1, db_path)
    recent_raw: list[Regime] = [today_raw] + [
        r.market_regime.raw_regime for r in older
    ]
    prev_confirmed = prev_report.market_regime.regime if prev_report else None
    market_regime: RegimeState = regime_mod.confirm(recent_raw, prev_confirmed)

    # 7) Alerts -------------------------------------------------------------
    all_alerts: list[Alert] = []
    prev_results = prev_report.results if prev_report else {}
    for ticker, result in results.items():
        prev_res = prev_results.get(ticker)
        prev_ind: TickerIndicators | None = prev_res.indicators if prev_res else None
        all_alerts.extend(alerts.detect_alerts(result, prev_ind))

    recent5 = snapshot.load_recent(as_of, 5, db_path)
    prev_breadth = recent5[-1].breadth_pct if recent5 else None
    breadth_alert = alerts.detect_breadth_alert(breadth, prev_breadth)
    if breadth_alert is not None:
        all_alerts.append(breadth_alert)

    all_alerts.extend(alerts.detect_multi_asset_alerts(results))
    all_alerts.sort(key=lambda a: a.severity, reverse=True)

    # 8) Assemble -----------------------------------------------------------
    report = DailyReport(
        date=as_of,
        market_regime=market_regime,
        breadth_pct=breadth,
        vix=vix_latest,
        results=results,
        alerts=all_alerts,
    )

    # 9) Persist + render ---------------------------------------------------
    snapshot.save_report(report, db_path)
    source = "合成数据（离线）" if offline else "yfinance（Yahoo）"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    site_dir = os.path.dirname(os.path.abspath(output))

    # 近期快照（制度序列回填 + 状态机 + 解释层共用）。加载失败降级为空列表。
    try:
        recent = snapshot.load_recent(as_of, config.HISTORY_MAX_DAYS, db_path)
    except Exception as exc:  # noqa: BLE001
        _warn(f"近期快照加载失败（{exc!r}）")
        recent = []

    # 解释层：把今日结论/风险/机会/较昨日变化组织成人话（方案 §5）。纯派生，
    # 喂给数据契约与看板顶部。失败降级为 None，不影响其余产物。
    try:
        explain = explain_mod.build_explain(report, prev_report)
    except Exception as exc:  # noqa: BLE001
        _warn(f"解释层生成失败（{exc!r}）")
        explain = None

    # 制度状态机：当前制度已持续多久、上次何时因何切换（方案 §6）。纯派生。
    try:
        state = state_machine.build_state(report, recent)
    except Exception as exc:  # noqa: BLE001
        _warn(f"制度状态机生成失败（{exc!r}）")
        state = None

    # 历史相似状态：当时状态和今天最像的历史日子（方案 §7，仅描述、无前向）。
    try:
        similar = similarity.build_similar(report, recent)
    except Exception as exc:  # noqa: BLE001
        _warn(f"相似状态生成失败（{exc!r}）")
        similar = None

    # 算法原理页（数据无关，总是生成）。
    try:
        about.write_about_page(os.path.join(site_dir, "about.html"),
                               source=source, generated_at=generated_at)
    except Exception as exc:  # noqa: BLE001
        _warn(f"算法原理页生成失败（{exc!r}）")

    # 每只自选股一页历史详情（价格趋势 + 制度底色 + 关键节点）。
    detail_links: dict[str, str] = {}
    if write_details:
        try:
            detail_links = detail.render_detail_pages(
                frames, benchmark_df, vix_frame["Close"] if vix_frame is not None and "Close" in vix_frame else None,
                stocks, site_dir, results, source=source, generated_at=generated_at,
            )
        except Exception as exc:  # noqa: BLE001 — 详情页失败不影响看板
            _warn(f"个股详情页生成失败（{exc!r}）")

    # 看板视图模型：算一次，让 HTML 与 dashboard_view.json 同源（方案 §6）。展示逻辑
    # （配色/格式化/标签/排序）都在此折叠成 JSON-safe 数据，供 PWA / memo / API 复用。
    try:
        view_model = dashboard.build_view_model(
            report, prev_report, source=source, generated_at=generated_at,
            detail_links=detail_links, explain=explain, state=state, similar=similar,
        )
    except Exception as exc:  # noqa: BLE001
        _warn(f"视图模型构建失败（{exc!r}）")
        view_model = None

    # 数据产物层：把今日报告 + 视图模型发布成 public/data/*.json 公开契约（方案 §2）。
    # 静态页面消费这些 JSON，而非直接依赖 Python 对象。失败不影响看板生成。
    try:
        artifacts.write_artifacts(
            report, prev_report,
            data_dir=os.path.join(site_dir, "data"),
            source=source, generated_at=generated_at,
            stocks=stocks, recent_reports=recent, explain=explain, state=state,
            similar=similar, view_model=view_model,
        )
    except Exception as exc:  # noqa: BLE001 — 数据产物失败不影响看板
        _warn(f"数据产物生成失败（{exc!r}）")

    # 看板（主产物）：复用同一 view_model 渲染，避免重算。
    dashboard.write_dashboard(
        report, prev_report, output,
        source=source, generated_at=generated_at, detail_links=detail_links,
        explain=explain, state=state, view_model=view_model,
    )
    return report
