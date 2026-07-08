"""Per-ticker detail pages: generation + dashboard linking (offline)."""
from atlas import runner
from atlas.detail import safe_name


def test_safe_name_sanitizes_tickers():
    assert safe_name("AAPL") == "AAPL"
    assert safe_name("1810.HK") == "1810_HK"
    assert safe_name("159605.SZ") == "159605_SZ"


def test_detail_pages_generated_and_linked(tmp_path):
    out = tmp_path / "index.html"
    runner.run(offline=True, db_path=str(tmp_path / "s.sqlite"), output=str(out))

    pages = list((tmp_path / "t").glob("*.html"))
    assert pages, "expected per-ticker detail pages under t/"

    page = pages[0].read_text(encoding="utf-8")
    assert page.startswith("<!DOCTYPE html>") and page.rstrip().endswith("</html>")
    assert "<svg" in page                 # trend chart
    assert "返回看板" in page              # back link
    assert "关键节点" in page              # event section

    dash = out.read_text(encoding="utf-8")
    assert 'class="tklink"' in dash and "t/" in dash   # dashboard links to details
