from __future__ import annotations

import pandas as pd
import pytest

from capybara_fetcher.pipeline.release_ingest import ReleaseInfo, _feature_to_std_df, load_release_collection


def test_feature_to_std_df_requires_columns():
    df = pd.DataFrame({"Date": ["2026-01-01"], "Ticker": ["005930"]})
    with pytest.raises(ValueError):
        _feature_to_std_df(df)


def test_load_release_collection_maps_and_filters(monkeypatch):
    feature = pd.DataFrame(
        {
            "Date": ["2025-01-01", "2025-01-02"],
            "Ticker": ["5930", "5930"],
            "Open": [100, 110],
            "High": [120, 130],
            "Low": [90, 100],
            "Close": [110, 120],
            "Volume": [1000, 2000],
        }
    )
    master = pd.DataFrame(
        {
            "Code": ["005930"],
            "Name": ["SamsungElec"],
            "Market": ["KOSPI"],
            "IndustryLarge": ["IT"],
            "IndustryMid": ["Semiconductor"],
            "IndustrySmall": ["Memory"],
            "SharesOutstanding": [1000000],
        }
    )

    def fake_resolve_release(repo: str, *, tag: str | None, token: str | None):
        return (
            ReleaseInfo(repo=repo, tag=tag or "latest", name="rel", published_at="2026-06-10T00:00:00Z"),
            {
                "korea_universe_feature_frame.parquet": "url://feature",
                "krx_stock_master.parquet": "url://master",
            },
        )

    def fake_read_parquet_url(url: str, *, token: str | None = None):
        if url.endswith("feature"):
            return feature
        if url.endswith("master"):
            return master
        raise AssertionError("unexpected url")

    monkeypatch.setattr("capybara_fetcher.pipeline.release_ingest._resolve_release", fake_resolve_release)
    monkeypatch.setattr("capybara_fetcher.pipeline.release_ingest._read_parquet_url", fake_read_parquet_url)

    loaded = load_release_collection(
        repo="capybara-dance/capybara_fetcher",
        tag="data-20250102-0000",
        token=None,
        start_date="2025-01-02",
        end_date="2025-01-02",
    )

    assert loaded.release.tag == "data-20250102-0000"
    assert len(loaded.result.master_df) == 1
    assert len(loaded.result.industry_df) == 1
    assert len(loaded.result.price_df) == 1
    assert loaded.result.price_df.iloc[0]["TICKER"] == "005930"
    assert float(loaded.result.price_df.iloc[0]["MARKET_CAP"]) == 120 * 1000000
    assert loaded.result.dividend_df.empty


def test_load_release_collection_requires_assets(monkeypatch):
    def fake_resolve_release(repo: str, *, tag: str | None, token: str | None):
        return (
            ReleaseInfo(repo=repo, tag="latest", name="rel", published_at=None),
            {"something.parquet": "url://other"},
        )

    monkeypatch.setattr("capybara_fetcher.pipeline.release_ingest._resolve_release", fake_resolve_release)

    with pytest.raises(RuntimeError):
        load_release_collection(repo="capybara-dance/capybara_fetcher", tag=None, token=None)
