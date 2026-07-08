"""Offline tests for the data layer's Stooq fallback parsing (no network)."""
import pandas as pd

from atlas import data_fetch as d


def test_stooq_symbol_mapping():
    assert d._stooq_symbol("SPY") == "spy.us"
    assert d._stooq_symbol("AAPL") == "aapl.us"
    assert d._stooq_symbol("^VIX") == "^vix"


def _sample_csv(n: int = 28) -> str:
    rows = "\n".join(
        f"2026-06-{i:02d},{100 + i},{101 + i},{99 + i},{100 + i}.5,{1_000_000 + i}"
        for i in range(1, n + 1)
    )
    return "Date,Open,High,Low,Close,Volume\n" + rows + "\n"


def test_parse_stooq_csv_basic():
    df = d._parse_stooq_csv(_sample_csv(), "SPY", "1y")
    assert df is not None
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.is_monotonic_increasing
    assert not df["Close"].isna().any()


def test_parse_stooq_csv_drops_bad_rows():
    csv = _sample_csv() + "2026-07-01,N/D,N/D,N/D,N/D,N/D\n"
    df = d._parse_stooq_csv(csv, "SPY", "1y")
    assert df is not None and not df["Close"].isna().any()


def test_parse_stooq_csv_guards():
    assert d._parse_stooq_csv("", "X", "1y") is None
    assert d._parse_stooq_csv("<html>No data</html>", "X", "1y") is None
    assert d._parse_stooq_csv("garbage,not,csv", "X", "1y") is None


def test_slice_period_trims_history():
    big = pd.DataFrame(
        {c: [1.0] * 800 for c in ["Open", "High", "Low", "Close", "Volume"]},
        index=pd.bdate_range(end="2026-07-08", periods=800),
    )
    out = d._slice_period(big, "1y")
    assert 240 <= len(out) <= 275  # ~1 year of business days
