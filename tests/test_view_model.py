"""看板视图模型 + dashboard_view.json 测试 (方案 §6).

守住核心不变量：**HTML 与 dashboard_view.json 同源** —— 用发布出去的 JSON 视图
模型重新渲染，能逐字节复现 index.html。这样 PWA / memo / API 消费 JSON 即等价于
看板，不会各自重算出不一致的结果。
"""
from __future__ import annotations

import json
import os

from atlas import runner
from atlas.dashboard import build_view_model, render_view


def _run(tmp_path):
    db = str(tmp_path / "snap.sqlite")
    out = str(tmp_path / "site" / "index.html")
    report = runner.run(offline=True, write_details=False, db_path=db, output=out, as_of="2026-07-06")
    data_dir = os.path.join(os.path.dirname(out), "data")
    return report, out, data_dir


def test_dashboard_view_is_written_and_registered(tmp_path):
    _, _, data_dir = _run(tmp_path)
    vm_path = os.path.join(data_dir, "dashboard_view.json")
    assert os.path.exists(vm_path)
    manifest = json.load(open(os.path.join(data_dir, "manifest.json"), encoding="utf-8"))
    assert manifest["files"]["dashboard_view"] == "dashboard_view.json"


def test_html_and_json_are_same_source(tmp_path):
    _, out, data_dir = _run(tmp_path)
    with open(out, encoding="utf-8") as fh:
        html_on_disk = fh.read()
    with open(os.path.join(data_dir, "dashboard_view.json"), encoding="utf-8") as fh:
        view_model = json.load(fh)
    # 用发布的 JSON 视图模型重渲染 → 必须与磁盘上的 index.html 完全一致。
    assert render_view(view_model) == html_on_disk


def test_risk_shown_as_band_with_flags(tmp_path):
    """R 以三档呈现（数字为辅），并带出可追溯的否决条目 —— 方案一。"""
    _, _, data_dir = _run(tmp_path)
    with open(os.path.join(data_dir, "dashboard_view.json"), encoding="utf-8") as fh:
        vm = json.load(fh)
    assert "风险档位" in vm["risk_legend"]
    for row in vm["markets"] + vm["stocks"]:
        assert row["risk"]["label"] in ("低风险", "中风险", "高风险")
        assert isinstance(row["risk_flags"], list)
    # 大盘行保留原始 R 数值字段（契约只增不减）。
    assert all("R" in m for m in vm["markets"])


def test_gold_is_in_watchlist_and_displayed_as_cny_per_gram(tmp_path):
    report, out, data_dir = _run(tmp_path)
    gold = report.results["GC=F"]
    with open(os.path.join(data_dir, "dashboard_view.json"), encoding="utf-8") as fh:
        vm = json.load(fh)
    row = next(s for s in vm["stocks"] if s["ticker"] == "GC=F")

    assert gold.name == "黄金"
    assert row["price_unit"] == "元/克"
    assert "元/克" in open(out, encoding="utf-8").read()

    universe = json.load(open(os.path.join(data_dir, "universe.json"), encoding="utf-8"))
    gold_meta = next(s for s in universe["layers"]["stock"] if s["ticker"] == "GC=F")
    assert gold_meta["price_unit"] == "元/克"


def test_view_model_is_json_serializable_and_has_expected_shape():
    from atlas.types import DailyReport, Regime, RegimeState
    report = DailyReport(
        date="2026-07-06",
        market_regime=RegimeState(regime=Regime.RISK_ON, raw_regime=Regime.RISK_ON,
                                  prev_regime=None, changed=False, reason="首次记录"),
        breadth_pct=0.5, vix=18.0, results={}, alerts=[],
    )
    vm = build_view_model(report, None, source="测试", generated_at="2026-07-06 00:00 UTC")
    # 纯 JSON-safe：不抛异常即证明只含 str/dict/list/bool/None。
    json.dumps(vm, ensure_ascii=False)
    for key in ("date", "regime", "markets", "sectors", "multi_assets", "stocks", "alerts"):
        assert key in vm
    assert vm["date"] == "2026-07-06"
