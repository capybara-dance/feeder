import pytest
import pandas as pd

from capybara_fetcher.standardize import standardize_ohlcv


def test_standardize_pykrx_like_frame():
    idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
    raw = pd.DataFrame(
        {
            "시가": [100, 110],
            "고가": [120, 115],
            "저가": [90, 105],
            "종가": [110, 112],
            "거래량": [1000, 2000],
            "거래대금": [123_000, 234_000],
            "등락률": [1.0, 0.5],
        },
        index=idx,
    )

    out = standardize_ohlcv(raw, ticker="5930")  # will zfill
    assert list(out.columns) == ["Date", "Open", "High", "Low", "Close", "Volume", "TradingValue", "Change", "Ticker"]
    assert out["Ticker"].unique().tolist() == ["005930"]
    assert out["Date"].iloc[0] == pd.Timestamp("2025-01-02")
    assert out["Close"].iloc[-1] == 112


def test_standardize_raises_on_empty():
    with pytest.raises(ValueError):
        standardize_ohlcv(pd.DataFrame(), ticker="000000")

