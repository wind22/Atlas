"""CLI 入口 (方案 §5).

流水线编排已迁到 :mod:`atlas.pipelines.daily`；本模块只保留 argparse CLI，并
re-export ``run`` 以兼容既有 ``from atlas import runner; runner.run(...)`` 用法。
离线（确定性合成数据）与在线（yfinance）两条路径都跑同一份评分代码。
"""
from __future__ import annotations

import argparse
import sys

from . import config
from .pipelines.daily import run
from .types import REGIME_LIGHT

__all__ = ["run", "main"]


def _warn(msg: str) -> None:
    print(f"[atlas] {msg}", file=sys.stderr)


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
