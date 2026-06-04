import os

import pytest

from capybara_fetcher.providers import PykrxProvider
from capybara_fetcher.standardize import standardize_ohlcv


@pytest.mark.external
def test_pykrx_provider_smoke():
    """
    External smoke test (network required).
    Runs only when RUN_EXTERNAL_SMOKE=1.
    """
    if os.getenv("RUN_EXTERNAL_SMOKE") != "1":
        pytest.skip("set RUN_EXTERNAL_SMOKE=1 to run external smoke tests")

    p = PykrxProvider(master_json_path="data/krx_stock_master.json")
    tickers, _ = p.list_tickers()
    assert tickers, "provider returned no tickers"

    # Benchmark should always exist
    raw = p.fetch_ohlcv(ticker="069500", start_date="20250101", end_date="20250110", adjusted=True)
    std = standardize_ohlcv(raw, ticker="069500")
    assert not std.empty

