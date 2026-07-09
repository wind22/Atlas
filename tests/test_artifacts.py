"""数据产物层契约测试 (方案 §2 / §9).

守住三件事：
  1. runner 每天真的把 public/data/*.json 写出来，且结构与 schema 对齐。
  2. latest.json 能无损还原成 DailyReport（契约是 to_dict() 的稳定外壳）。
  3. 铁律 Ⅱ「不预测，只响应」：契约里不出现任何前向收益 / 预测字段。

黑盒风格与其余测试一致：用 runner.run(offline=True) 造真实报告，不 mock 内部。
"""
from __future__ import annotations

import json
import os

from atlas import runner
from atlas.storage import artifacts
from atlas.types import DailyReport


def _load(path: str) -> object:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _run(tmp_path, as_of: str):
    db = str(tmp_path / "snap.sqlite")
    out = str(tmp_path / "site" / "index.html")
    report = runner.run(
        offline=True, write_details=False, db_path=db, output=out, as_of=as_of
    )
    data_dir = os.path.join(os.path.dirname(out), "data")
    return report, data_dir


# --------------------------------------------------------------------------
# 1) 文件确实产出 + 结构正确
# --------------------------------------------------------------------------
def test_run_writes_full_data_contract(tmp_path):
    report, data_dir = _run(tmp_path, "2026-07-06")

    for name in ("latest.json", "universe.json", "regime_history.json",
                 "manifest.json", "schema.json"):
        assert os.path.exists(os.path.join(data_dir, name)), f"missing {name}"
    assert os.path.exists(os.path.join(data_dir, "daily", "2026-07-06.json"))

    envelope = _load(os.path.join(data_dir, "latest.json"))
    assert envelope["schema_version"] == artifacts.SCHEMA_VERSION
    assert envelope["meta"]["date"] == report.date
    assert envelope["meta"]["prev_date"] is None  # cold DB → no predecessor
    assert "report" in envelope


def test_latest_and_daily_are_identical(tmp_path):
    _, data_dir = _run(tmp_path, "2026-07-06")
    latest = _load(os.path.join(data_dir, "latest.json"))
    daily = _load(os.path.join(data_dir, "daily", "2026-07-06.json"))
    assert latest == daily


# --------------------------------------------------------------------------
# 2) 契约无损还原 → DailyReport
# --------------------------------------------------------------------------
def test_envelope_report_roundtrips_to_dailyreport(tmp_path):
    report, data_dir = _run(tmp_path, "2026-07-06")
    envelope = _load(os.path.join(data_dir, "latest.json"))
    restored = DailyReport.from_dict(envelope["report"])
    assert restored.date == report.date
    assert restored.market_regime.regime == report.market_regime.regime
    assert set(restored.results) == set(report.results)


# --------------------------------------------------------------------------
# 3) universe / manifest / regime_history 内容
# --------------------------------------------------------------------------
def test_universe_lists_four_layers(tmp_path):
    _, data_dir = _run(tmp_path, "2026-07-06")
    uni = _load(os.path.join(data_dir, "universe.json"))
    assert set(uni["layers"]) == {"market", "sector", "multi_asset", "stock"}
    assert uni["benchmark"] == "SPY"
    market_tickers = {row["ticker"] for row in uni["layers"]["market"]}
    assert {"SPY", "QQQ"} <= market_tickers


def test_regime_history_accumulates_across_runs(tmp_path):
    db = str(tmp_path / "snap.sqlite")
    out = str(tmp_path / "site" / "index.html")
    data_dir = os.path.join(os.path.dirname(out), "data")
    for d in ("2026-07-06", "2026-07-07", "2026-07-08"):
        runner.run(offline=True, write_details=False, db_path=db, output=out, as_of=d)

    history = _load(os.path.join(data_dir, "regime_history.json"))
    dates = [rec["date"] for rec in history]
    assert dates == ["2026-07-06", "2026-07-07", "2026-07-08"]  # ascending, deduped
    for rec in history:
        assert set(rec) >= {"date", "regime", "raw_regime", "breadth_pct"}

    manifest = _load(os.path.join(data_dir, "manifest.json"))
    assert manifest["latest"] == "2026-07-08"
    assert manifest["dates"] == ["2026-07-06", "2026-07-07", "2026-07-08"]


def test_rerunning_same_date_does_not_duplicate_history(tmp_path):
    db = str(tmp_path / "snap.sqlite")
    out = str(tmp_path / "site" / "index.html")
    data_dir = os.path.join(os.path.dirname(out), "data")
    runner.run(offline=True, write_details=False, db_path=db, output=out, as_of="2026-07-06")
    runner.run(offline=True, write_details=False, db_path=db, output=out, as_of="2026-07-06")
    history = _load(os.path.join(data_dir, "regime_history.json"))
    assert [r["date"] for r in history] == ["2026-07-06"]


# --------------------------------------------------------------------------
# 铁律 Ⅱ：契约禁止任何前向 / 预测字段
# --------------------------------------------------------------------------
_FORBIDDEN = ("next_30d", "forecast", "prediction", "predicted",
              "expected_return", "target_price", "buy", "sell", "future")


def test_contract_contains_no_forward_looking_fields(tmp_path):
    _, data_dir = _run(tmp_path, "2026-07-06")
    for name in ("latest.json", "regime_history.json", "universe.json",
                 "manifest.json", "schema.json"):
        with open(os.path.join(data_dir, name), encoding="utf-8") as fh:
            raw = fh.read()
        # schema.json 的 description 里会解释「禁止 buy/sell」等，属元描述，跳过键名扫描。
        obj = json.loads(raw)
        _assert_no_forbidden_keys(obj, name)


def _assert_no_forbidden_keys(obj, where: str) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k.lower() not in _FORBIDDEN, f"forbidden forward-looking key {k!r} in {where}"
            _assert_no_forbidden_keys(v, where)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_forbidden_keys(item, where)
