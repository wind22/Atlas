"""Algorithm-explainer page: reflects config + is generated & linked."""
from atlas import config, runner
from atlas.about import render_about_page


def test_about_reflects_config():
    h = render_about_page()
    assert h.startswith("<!DOCTYPE html>") and h.rstrip().endswith("</html>")
    # thresholds/weights pulled from config (stay in sync)
    for v in (config.T_STRONG, config.R_HIGH, config.T_WEAK, config.R_LOW,
              config.DIR_ABOVE_MA200, config.MA_LONG, config.ADX_TREND,
              config.REGIME_CONFIRM_DAYS, config.RISK_BELOW_MA200):
        assert str(v) in h
    assert "三条铁律" in h and "返回看板" in h and "backtest.html" in h


def test_runner_writes_and_links_about(tmp_path):
    out = tmp_path / "index.html"
    runner.run(offline=True, write_details=False,
               db_path=str(tmp_path / "s.sqlite"), output=str(out))
    assert (tmp_path / "about.html").exists()
    assert 'href="about.html"' in out.read_text(encoding="utf-8")
