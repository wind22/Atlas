"""Offline tests for the data layer's Stooq fallback parsing (no network)."""
import pandas as pd
import pytest

from atlas import data_fetch as d
from atlas import config


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


def _stepped_frame(step: float, split_at: int = 20, n: int = 40):
    idx = pd.bdate_range(end="2026-07-08", periods=n)
    close = [4.0] * split_at + [4.0 * step] * (n - split_at)
    return pd.DataFrame(
        {"Open": close, "High": [c * 1.01 for c in close],
         "Low": [c * 0.99 for c in close], "Close": close, "Volume": [1e6] * n},
        index=idx,
    )


def test_backadjust_splits_a_share_step():
    # 沪市 ETF 送转导致的除权台阶(4.0 → ~1.05, 10送~28)必须被反向复权拼接连续
    df = _stepped_frame(0.263)
    out = d._backadjust_corporate_actions(df.copy(), "562590.SS")
    ratios = out["Close"].to_numpy()[1:] / out["Close"].to_numpy()[:-1]
    assert ratios.min() > 0.9 and ratios.max() < 1.1     # 台阶被抹平，序列连续
    assert abs(out["Close"].iloc[-1] - df["Close"].iloc[-1]) < 1e-9  # 除权后价格不变


def test_backadjust_leaves_us_crash_untouched():
    # 美股没有涨跌停，-74% 可能是真实暴跌——绝不能当成除权抹掉(铁律 Ⅰ)
    df = _stepped_frame(0.263)
    out = d._backadjust_corporate_actions(df.copy(), "AAPL")
    pd.testing.assert_frame_equal(out, df)               # 非沪深后缀，原样返回


def test_backadjust_ignores_normal_a_share_moves():
    # A股涨跌停内的正常波动(每日 ±3%)不应被误判为除权
    idx = pd.bdate_range(end="2026-07-08", periods=30)
    close = [3.0 * (1.03 ** i) for i in range(30)]        # 平滑上涨，无跳变
    df = pd.DataFrame({"Open": close, "High": close, "Low": close,
                       "Close": close, "Volume": [1e6] * 30}, index=idx)
    out = d._backadjust_corporate_actions(df.copy(), "159605.SZ")
    pd.testing.assert_frame_equal(out, df)


def test_slice_period_trims_history():
    big = pd.DataFrame(
        {c: [1.0] * 800 for c in ["Open", "High", "Low", "Close", "Volume"]},
        index=pd.bdate_range(end="2026-07-08", periods=800),
    )
    out = d._slice_period(big, "1y")
    assert 240 <= len(out) <= 275  # ~1 year of business days


def test_gold_to_cny_per_gram_converts_full_ohlc_and_preserves_volume():
    gold_idx = pd.to_datetime(["2026-07-06", "2026-07-07", "2026-07-08"])
    fx_idx = pd.to_datetime(["2026-07-06", "2026-07-08"])
    gold = pd.DataFrame(
        {
            "Open": [3000.0, 3100.0, 3200.0],
            "High": [3010.0, 3110.0, 3210.0],
            "Low": [2990.0, 3090.0, 3190.0],
            "Close": [3000.0, 3100.0, 3200.0],
            "Volume": [10.0, 20.0, 30.0],
        },
        index=gold_idx,
    )
    fx = pd.DataFrame({"Close": [7.0, 7.2]}, index=fx_idx)
    original_gold = gold.copy()

    out = d.gold_to_cny_per_gram(gold, fx)

    # 7 月 7 日无汇率 bar，沿用 7 月 6 日的已知收盘汇率。
    assert out.loc[gold_idx[1], "Close"] == pytest.approx(
        3100.0 * 7.0 / config.GRAMS_PER_TROY_OUNCE
    )
    assert out.loc[gold_idx[2], "High"] == pytest.approx(
        3210.0 * 7.2 / config.GRAMS_PER_TROY_OUNCE
    )
    assert out["Volume"].tolist() == gold["Volume"].tolist()
    pd.testing.assert_frame_equal(gold, original_gold)  # 输入未被原地修改


def test_gold_to_cny_per_gram_rejects_missing_close():
    idx = pd.to_datetime(["2026-07-06"])
    with pytest.raises(ValueError, match="Close"):
        d.gold_to_cny_per_gram(
            pd.DataFrame({"Open": [3000.0]}, index=idx),
            pd.DataFrame({"Close": [7.0]}, index=idx),
        )


def test_gold_conversion_never_backfills_with_future_fx():
    gold_idx = pd.to_datetime(["2026-07-03", "2026-07-06"])
    gold = pd.DataFrame(
        {c: [3000.0, 3010.0] for c in ["Open", "High", "Low", "Close"]}
        | {"Volume": [10.0, 11.0]},
        index=gold_idx,
    )
    fx = pd.DataFrame(
        {"Close": [7.0]}, index=pd.to_datetime(["2026-07-06"])
    )

    out = d.gold_to_cny_per_gram(gold, fx)

    assert out.index.tolist() == [pd.Timestamp("2026-07-06")]
