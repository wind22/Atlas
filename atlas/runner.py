"""Pipeline orchestration + CLI (架构 §2 数据流全链路).

``run()`` wires every module together for one trading day:
  data_fetch → indicators → scoring → regime + alerts → DailyReport
  → snapshot (persist) → dashboard (render). One bad ticker never aborts a
  run — per-ticker work is wrapped so the report degrades gracefully.

``main()`` is the argparse CLI. Both offline (deterministic synthetic prices)
and online (yfinance) paths run through the identical scoring code.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import pandas as pd

from . import config
from . import data_fetch, indicators, scoring, alerts, snapshot, dashboard, detail, about
from . import regime as regime_mod
from .storage import artifacts
from .report import explain as explain_mod
from .report import state_machine
from .config import Layer
from .types import (
    REGIME_LIGHT,
    Alert,
    DailyReport,
    Regime,
    RegimeState,
    TickerIndicators,
    TickerResult,
)

_VIX_LAYERS = (Layer.MARKET, Layer.MULTI_ASSET)


def _warn(msg: str) -> None:
    print(f"[runner] {msg}", file=sys.stderr)


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

    # 数据产物层：把今日报告发布成 public/data/*.json 公开契约（方案 §2）。
    # 静态页面消费这些 JSON，而非直接依赖 Python 对象。失败不影响看板生成。
    try:
        artifacts.write_artifacts(
            report, prev_report,
            data_dir=os.path.join(site_dir, "data"),
            source=source, generated_at=generated_at,
            stocks=stocks, recent_reports=recent, explain=explain, state=state,
        )
    except Exception as exc:  # noqa: BLE001 — 数据产物失败不影响看板
        _warn(f"数据产物生成失败（{exc!r}）")

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

    dashboard.write_dashboard(
        report, prev_report, output,
        source=source, generated_at=generated_at, detail_links=detail_links,
        explain=explain, state=state,
    )
    return report


def _parse_stocks(spec: str | None) -> dict[str, str] | None:
    """Turn a "AAPL,NVDA" comma list into {ticker: 中文名}; None if unset."""
    if not spec:
        return None
    tickers = [t.strip().upper() for t in spec.split(",") if t.strip()]
    if not tickers:
        return None
    return {t: config.name_of(t) for t in tickers}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, non-zero on failure."""
    parser = argparse.ArgumentParser(
        prog="atlas",
        description="Atlas 趋势风险与机会监控 — 运行每日流水线并生成看板",
    )
    parser.add_argument("--offline", action="store_true",
                        help="使用确定性合成数据，无需网络")
    parser.add_argument("--stocks", default=None,
                        help="自选个股，逗号分隔，如 AAPL,NVDA")
    parser.add_argument("--period", default=config.DEFAULT_PERIOD,
                        help=f"yfinance 历史窗口（默认 {config.DEFAULT_PERIOD}）")
    parser.add_argument("--db", default=config.DEFAULT_DB,
                        help=f"快照数据库路径（默认 {config.DEFAULT_DB}）")
    parser.add_argument("--output", default=config.DEFAULT_OUTPUT,
                        help=f"看板 HTML 输出路径（默认 {config.DEFAULT_OUTPUT}）")
    parser.add_argument("--date", default=None,
                        help="覆盖 as_of 日期（YYYY-MM-DD）")
    parser.add_argument("--no-details", action="store_true",
                        help="不生成每只自选股的历史详情页")
    args = parser.parse_args(argv)

    try:
        report = run(
            stocks=_parse_stocks(args.stocks),
            offline=args.offline,
            period=args.period,
            db_path=args.db,
            output=args.output,
            as_of=args.date,
            write_details=not args.no_details,
        )
    except Exception as exc:  # noqa: BLE001 — surface failure as a clean exit code
        _warn(f"运行失败：{exc!r}")
        return 1

    light = REGIME_LIGHT.get(report.market_regime.regime, "")
    spy = report.results.get(config.BENCHMARK)
    if spy is not None:
        tr = f"SPY T={spy.T:.0f}/R={spy.R:.0f}"
    else:
        tr = "SPY N/A"
    print(
        f"{light} {report.date} | {tr} | {len(report.alerts)} 条提示 "
        f"| 看板 -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
