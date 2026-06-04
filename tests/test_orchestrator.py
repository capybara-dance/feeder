import pandas as pd
import pytest

from capybara_fetcher.orchestrator import CacheBuildConfig, run_cache_build
from capybara_fetcher.provider import DataProvider


class FakeProvider(DataProvider):
    name = "fake"

    def __init__(self, *, fail_ticker: str | None = None):
        self._fail_ticker = fail_ticker

    def list_tickers(self, *, asof_date=None, market=None):
        return ["000001", "000002"], {"000001": "KOSPI", "000002": "KOSDAQ"}

    def load_stock_master(self, *, asof_date=None):
        return pd.DataFrame(
            {
                "Code": ["000001", "000002"],
                "Name": ["A", "B"],
                "Market": ["KOSPI", "KOSDAQ"],
                "IndustryLarge": ["L", "L"],
                "IndustryMid": ["M", "M"],
                "IndustrySmall": ["S", "S"],
                "SharesOutstanding": [1, 1],
            }
        )

    def fetch_ohlcv(self, *, ticker, start_date, end_date, adjusted=True):
        t = str(ticker).zfill(6)
        if self._fail_ticker and t == self._fail_ticker:
            raise RuntimeError("boom")
        # Return an already-standard-like raw frame (standardizer will accept Date column)
        dates = pd.date_range("2025-01-01", periods=260, freq="D")
        return pd.DataFrame(
            {
                "Date": dates,
                "Open": range(1, 261),
                "High": range(1, 261),
                "Low": range(1, 261),
                "Close": range(1, 261),
                "Volume": [100] * 260,
            }
        )


def test_orchestrator_success_writes_outputs(tmp_path):
    cfg = CacheBuildConfig(
        start_date="20250101",
        end_date="20251231",
        output_path=str(tmp_path / "feat.parquet"),
        meta_output_path=str(tmp_path / "feat.meta.json"),
        industry_output_path=None,
        industry_meta_output_path=None,
        industry_benchmark="universe",
        max_workers=2,
        test_limit=2,
        adjusted=True,
    )
    meta = run_cache_build(cfg, provider=FakeProvider())
    assert meta["run_status"] == "success"
    assert (tmp_path / "feat.parquet").exists()
    assert (tmp_path / "feat.meta.json").exists()


def test_orchestrator_fail_fast(tmp_path):
    cfg = CacheBuildConfig(
        start_date="20250101",
        end_date="20251231",
        output_path=str(tmp_path / "feat.parquet"),
        meta_output_path=str(tmp_path / "feat.meta.json"),
        industry_output_path=None,
        industry_meta_output_path=None,
        industry_benchmark="universe",
        max_workers=2,
        test_limit=2,
        adjusted=True,
    )
    with pytest.raises(Exception):
        run_cache_build(cfg, provider=FakeProvider(fail_ticker="000002"))
    # fail-fast: should not leave a parquet behind
    assert not (tmp_path / "feat.parquet").exists()

