"""数据产物层：把每日 :class:`DailyReport` 发布成公开 JSON 数据契约。

这是 Static-first Atlas v3 的地基（方案 §2）。静态页面（dashboard / detail /
PWA / 未来的 LLM memo）都只消费 ``public/data/*.json``，不再直接抱着 Python
``DailyReport`` 对象。产出的文件：

  * ``schema.json``           契约说明 + ``schema_version``（版本化，只增不改）。
  * ``latest.json``           今日完整报告（``DailyReport.to_dict()`` 外包一层 meta）。
  * ``daily/YYYY-MM-DD.json`` 单日报告，与 ``latest.json`` 同构。
  * ``regime_history.json``   制度时间序列（轻量，跨运行累积）。
  * ``universe.json``         四层 ticker → 中文名 → layer。
  * ``manifest.json``         可用日期列表 + latest 指针 + schema_version。

契约稳定性（**红线**）：``SCHEMA_VERSION`` 只增不改；字段只增不删不改类型；破坏性
变更必须升版本并在 ``schema.json`` 记迁移说明。铁律 Ⅱ「不预测，只响应」：契约里
**禁止**出现任何前向收益 / 买卖 / 价格目标字段——只描述「已经发生」的状态。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .. import config
from ..config import Layer
from ..types import DailyReport

# 契约版本。破坏性变更（删字段 / 改类型 / 改语义）时 +1，并更新 schema.json。
SCHEMA_VERSION = 1

# 四层的稳定展示顺序（universe.json / 前端分组用）。
_LAYER_ORDER: list[tuple[Layer, str]] = [
    (Layer.MARKET, "market"),
    (Layer.SECTOR, "sector"),
    (Layer.MULTI_ASSET, "multi_asset"),
    (Layer.STOCK, "stock"),
]


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _write_json(path: str, obj: object) -> None:
    """原子地写一份 UTF-8 JSON（先写临时文件再 rename，避免半截文件）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def _read_json(path: str) -> object | None:
    """读一份 JSON；文件不存在或损坏时返回 None（降级，不抛）。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _spy_scores(report: DailyReport) -> tuple[float | None, float | None]:
    """SPY 的 (T, R)，缺失时 (None, None)。制度序列的核心数值。"""
    spy = report.results.get(config.BENCHMARK)
    if spy is None:
        return None, None
    return float(spy.T), float(spy.R)


# --------------------------------------------------------------------------
# 单份报告的信封（latest.json / daily/*.json 同构）
# --------------------------------------------------------------------------
def build_report_envelope(
    report: DailyReport,
    prev_report: DailyReport | None,
    *,
    source: str | None,
    generated_at: str | None,
    explain: dict | None = None,
    state: dict | None = None,
    similar: dict | None = None,
) -> dict:
    """把 ``DailyReport.to_dict()`` 包成对外的稳定信封。

    ``explain`` 为解释层摘要（方案 §5）；``state`` 为制度持续状态（§6）；``similar``
    为历史相似状态（§7，仅描述、无前向）。均由 runner 计算后传入，本模块只负责嵌入
    —— storage 层保持纯序列化。
    """
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "date": report.date,
            "generated_at": generated_at or _now_utc(),
            "source": source or "yfinance",
            "prev_date": prev_report.date if prev_report else None,
        },
        "report": report.to_dict(),
    }
    if explain is not None:
        envelope["explain"] = explain
    if state is not None:
        envelope["state"] = state
    if similar is not None:
        envelope["similar"] = similar
    return envelope


# --------------------------------------------------------------------------
# 制度时间序列（跨运行累积）
# --------------------------------------------------------------------------
def _regime_record(report: DailyReport) -> dict:
    """单日制度快照：轻量、只含描述当日状态的字段（无任何前向信息）。"""
    t_spy, r_spy = _spy_scores(report)
    return {
        "date": report.date,
        "regime": report.market_regime.regime.value,
        "raw_regime": report.market_regime.raw_regime.value,
        "T_spy": t_spy,
        "R_spy": r_spy,
        "breadth_pct": report.breadth_pct,
        "vix": report.vix,
    }


def build_regime_history(
    report: DailyReport,
    existing: object | None,
    recent_reports: list[DailyReport] | None,
) -> list[dict]:
    """合并出升序的制度序列：既有文件 + 近期快照回填 + 今日，按 date upsert。

    以 date 为主键去重（今日覆盖同日旧值），最终按 date 升序。这样序列可以跨 CI
    运行累积（就像当前 SQLite 被 commit 回去那样），并能从近期快照自愈重建。
    """
    by_date: dict[str, dict] = {}
    if isinstance(existing, list):
        for rec in existing:
            if isinstance(rec, dict) and isinstance(rec.get("date"), str):
                by_date[rec["date"]] = rec
    for r in recent_reports or []:
        by_date[r.date] = _regime_record(r)
    by_date[report.date] = _regime_record(report)  # 今日永远最新
    return [by_date[d] for d in sorted(by_date)]


# --------------------------------------------------------------------------
# universe.json（四层 ticker 目录）
# --------------------------------------------------------------------------
def build_universe(stocks: dict[str, str] | None) -> dict:
    """从 config 导出四层 ticker → 中文名。自选层用运行时传入的 stocks。"""
    tables: dict[str, dict[str, str]] = {
        "market": dict(config.MARKET_TICKERS),
        "sector": dict(config.SECTOR_TICKERS),
        "multi_asset": dict(config.MULTI_ASSET_TICKERS),
        "stock": dict(stocks or config.DEFAULT_STOCKS),
    }
    layers = {
        key: [{"ticker": t, "name": n, "price_unit": config.price_unit_of(t)}
              for t, n in tables[key].items()]
        for _, key in _LAYER_ORDER
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_utc(),
        "benchmark": config.BENCHMARK,
        "vix": config.VIX_TICKER,
        "layers": layers,
    }


# --------------------------------------------------------------------------
# manifest.json（站点数据索引）
# --------------------------------------------------------------------------
def _available_daily_dates(daily_dir: str, latest_date: str) -> list[str]:
    """daily/ 下已有的 YYYY-MM-DD.json 日期，并入今日，升序去重。"""
    dates: set[str] = {latest_date}
    if os.path.isdir(daily_dir):
        for fn in os.listdir(daily_dir):
            if fn.endswith(".json"):
                dates.add(fn[: -len(".json")])
    return sorted(dates)


def build_manifest(latest_date: str, dates: list[str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_utc(),
        "latest": latest_date,
        "dates": dates,
        "files": {
            "schema": "schema.json",
            "latest": "latest.json",
            "universe": "universe.json",
            "regime_history": "regime_history.json",
            "daily": "daily/{date}.json",
            "dashboard_view": "dashboard_view.json",
        },
    }


# --------------------------------------------------------------------------
# schema.json（契约自述，版本化）
# --------------------------------------------------------------------------
def build_schema() -> dict:
    """人类可读的契约说明。字段随契约演进增补；破坏性变更须升 SCHEMA_VERSION。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_utc(),
        "description": (
            "Atlas 每日数据契约。静态页面只消费这些文件，不直接依赖 Python 对象。"
            "铁律 Ⅱ：契约禁止任何前向收益 / 买卖 / 价格目标字段——只描述已发生的状态。"
        ),
        "stability": (
            "schema_version 只增不改；字段只增不删不改类型；破坏性变更须升版本。"
        ),
        "files": {
            "latest.json / daily/{date}.json": {
                "schema_version": "int，契约版本",
                "meta": "{date, generated_at, source, prev_date}",
                "report": "DailyReport.to_dict()：market_regime / breadth_pct / vix / results / alerts",
                "explain": "解释层：headline / top_risks / top_opportunities / delta_from_yesterday",
                "state": "制度状态机：current_regime / days_in_regime / previous_regime / last_transition_date / transition_reason",
                "similar": (
                    "历史相似状态：similar_periods:[{date, regime, T_spy, R_spy, breadth_pct,"
                    " vix, distance}]。仅描述当时状态，铁律 Ⅱ 禁止任何前向收益字段"
                ),
            },
            "regime_history.json": (
                "升序数组，每项 {date, regime, raw_regime, T_spy, R_spy, breadth_pct, vix}"
            ),
            "universe.json": "{benchmark, vix, layers:{market,sector,multi_asset,stock:[{ticker,name,price_unit}]}}",
            "manifest.json": "{latest, dates:[...], files:{...}} —— 站点数据索引",
            "dashboard_view.json": (
                "看板视图模型：展示逻辑算好的 JSON-safe 数据（配色/格式化/标签/排序），"
                "HTML 与之同源，供 PWA / memo / API 复用"
            ),
        },
    }


# --------------------------------------------------------------------------
# 公共 API：写全套数据产物
# --------------------------------------------------------------------------
def write_artifacts(
    report: DailyReport,
    prev_report: DailyReport | None,
    *,
    data_dir: str,
    source: str | None = None,
    generated_at: str | None = None,
    stocks: dict[str, str] | None = None,
    recent_reports: list[DailyReport] | None = None,
    explain: dict | None = None,
    state: dict | None = None,
    similar: dict | None = None,
    view_model: dict | None = None,
) -> dict[str, str]:
    """把 ``report`` 发布成 ``data_dir`` 下的整套 JSON 契约。

    返回 {逻辑名: 绝对路径}。任一文件写失败会抛异常，调用方（runner）已用
    try/except 兜底——数据产物失败不影响 HTML 看板照常生成。
    """
    generated_at = generated_at or _now_utc()
    daily_dir = os.path.join(data_dir, "daily")

    envelope = build_report_envelope(
        report, prev_report, source=source, generated_at=generated_at,
        explain=explain, state=state, similar=similar,
    )
    history = build_regime_history(
        report, _read_json(os.path.join(data_dir, "regime_history.json")), recent_reports
    )
    universe = build_universe(stocks)
    dates = _available_daily_dates(daily_dir, report.date)
    manifest = build_manifest(report.date, dates)
    schema = build_schema()

    paths = {
        "latest": os.path.join(data_dir, "latest.json"),
        "daily": os.path.join(daily_dir, f"{report.date}.json"),
        "regime_history": os.path.join(data_dir, "regime_history.json"),
        "universe": os.path.join(data_dir, "universe.json"),
        "manifest": os.path.join(data_dir, "manifest.json"),
        "schema": os.path.join(data_dir, "schema.json"),
    }
    _write_json(paths["latest"], envelope)
    _write_json(paths["daily"], envelope)
    _write_json(paths["regime_history"], history)
    _write_json(paths["universe"], universe)
    _write_json(paths["manifest"], manifest)
    _write_json(paths["schema"], schema)
    # 看板视图模型（可选）：HTML 与它同源，供 PWA / memo / API 复用（方案 §6）。
    if view_model is not None:
        paths["dashboard_view"] = os.path.join(data_dir, "dashboard_view.json")
        _write_json(paths["dashboard_view"], view_model)
    return {k: os.path.abspath(v) for k, v in paths.items()}
