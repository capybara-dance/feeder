"""
Tests for CompositeProvider.
"""
import pytest
from capybara_fetcher.providers import CompositeProvider


def test_composite_provider_name():
    """Test that CompositeProvider has correct name."""
    composite = CompositeProvider()
    assert composite.name == "composite"


def test_composite_provider_is_dataclass_frozen():
    """Test that CompositeProvider is a frozen dataclass."""
    from dataclasses import FrozenInstanceError
    
    composite = CompositeProvider()
    
    # Should not be able to modify attributes (frozen dataclass)
    with pytest.raises(FrozenInstanceError):
        composite.name = "modified"


def test_composite_provider_implements_data_provider_protocol():
    """Test that CompositeProvider implements DataProvider protocol."""
    composite = CompositeProvider()
    
    # Check that it has required attributes
    assert hasattr(composite, "name")
    
    # Check that it has required methods
    assert hasattr(composite, "list_tickers")
    assert hasattr(composite, "load_stock_master")
    assert hasattr(composite, "fetch_ohlcv")
    
    # Check that methods are callable
    assert callable(composite.list_tickers)
    assert callable(composite.load_stock_master)
    assert callable(composite.fetch_ohlcv)


def test_composite_provider_list_tickers():
    """
    Test that list_tickers returns tickers and market mapping.
    
    Expected behavior (same as PykrxProvider):
    - Returns tuple of (tickers list, market_by_ticker dict)
    - All tickers are 6-digit strings
    - tickers list is sorted
    - market_by_ticker maps ticker -> market
    - All tickers in market_by_ticker keys should be in tickers list
    """
    from pathlib import Path
    
    composite = CompositeProvider()
    
    tickers, market_by_ticker = composite.list_tickers()
    
    # Check return types
    assert isinstance(tickers, list), "tickers should be a list"
    assert isinstance(market_by_ticker, dict), "market_by_ticker should be a dict"
    
    # Check tickers list
    assert len(tickers) > 0, "Should return at least one ticker"
    assert all(len(t) == 6 for t in tickers), "All tickers should be 6 digits"
    assert tickers == sorted(tickers), "tickers list should be sorted"
    
    # Check market_by_ticker
    assert len(market_by_ticker) > 0, "Should return at least one market mapping"
    
    # Check that all tickers in market_by_ticker are in tickers list
    ticker_set = set(tickers)
    for ticker in market_by_ticker.keys():
        assert ticker in ticker_set, f"Ticker {ticker} in market_by_ticker should be in tickers list"
    
    # Check that market values are valid (KOSPI or KOSDAQ typically)
    valid_markets = {"KOSPI", "KOSDAQ"}
    for ticker, market in market_by_ticker.items():
        assert isinstance(market, str), f"Market for {ticker} should be a string"
        # Market can be KOSPI, KOSDAQ, or other, so we just check it's a non-empty string
        assert len(market) > 0, f"Market for {ticker} should not be empty"


def test_composite_provider_list_tickers_by_market():
    """
    Test that list_tickers filters by market correctly.
    
    Expected behavior (same as PykrxProvider):
    - Returns filtered tickers when market parameter is provided
    - KOSPI and KOSDAQ should return different ticker lists
    - All returned tickers should belong to the specified market
    - market_by_ticker values should match the filter
    """
    composite = CompositeProvider()
    
    # Get all tickers for comparison
    all_tickers, all_market_by_ticker = composite.list_tickers()
    
    # Test KOSPI filter
    tickers_kospi, market_by_ticker_kospi = composite.list_tickers(market="KOSPI")
    assert len(tickers_kospi) > 0, "Should return KOSPI tickers"
    assert all(len(t) == 6 for t in tickers_kospi), "All KOSPI tickers should be 6 digits"
    assert tickers_kospi == sorted(tickers_kospi), "KOSPI tickers should be sorted"
    
    # All KOSPI tickers should be in the all_tickers list
    all_ticker_set = set(all_tickers)
    for ticker in tickers_kospi:
        assert ticker in all_ticker_set, f"KOSPI ticker {ticker} should be in all tickers"
    
    # All market_by_ticker values should be "KOSPI"
    for ticker, market in market_by_ticker_kospi.items():
        assert market == "KOSPI", f"Ticker {ticker} should have market KOSPI, got {market}"
    
    # Test KOSDAQ filter
    tickers_kosdaq, market_by_ticker_kosdaq = composite.list_tickers(market="KOSDAQ")
    assert len(tickers_kosdaq) > 0, "Should return KOSDAQ tickers"
    assert all(len(t) == 6 for t in tickers_kosdaq), "All KOSDAQ tickers should be 6 digits"
    assert tickers_kosdaq == sorted(tickers_kosdaq), "KOSDAQ tickers should be sorted"
    
    # All KOSDAQ tickers should be in the all_tickers list
    for ticker in tickers_kosdaq:
        assert ticker in all_ticker_set, f"KOSDAQ ticker {ticker} should be in all tickers"
    
    # All market_by_ticker values should be "KOSDAQ"
    for ticker, market in market_by_ticker_kosdaq.items():
        assert market == "KOSDAQ", f"Ticker {ticker} should have market KOSDAQ, got {market}"
    
    # KOSPI and KOSDAQ should have different ticker counts
    assert len(tickers_kospi) != len(tickers_kosdaq), "KOSPI and KOSDAQ should have different ticker counts"
    
    # KOSPI and KOSDAQ tickers should not overlap
    kospi_set = set(tickers_kospi)
    kosdaq_set = set(tickers_kosdaq)
    assert kospi_set.isdisjoint(kosdaq_set), "KOSPI and KOSDAQ tickers should not overlap"


def test_composite_provider_load_stock_master():
    """
    Test that load_stock_master returns stock master DataFrame.
    
    Expected behavior when implemented:
    - Returns pandas DataFrame
    - Contains required columns: Code, Name, Market, IndustryLarge, IndustryMid, IndustrySmall, SharesOutstanding
    - DataFrame is not empty
    """
    import pandas as pd
    
    composite = CompositeProvider()
    
    master = composite.load_stock_master()
    
    assert isinstance(master, pd.DataFrame), "Should return pandas DataFrame"
    assert not master.empty, "Stock master should not be empty"
    
    # Check required columns
    required_cols = ["Code", "Name", "Market", "IndustryLarge", "IndustryMid", "IndustrySmall", "SharesOutstanding"]
    for col in required_cols:
        assert col in master.columns, f"Required column {col} missing"


def test_composite_provider_fetch_ohlcv():
    """
    Test that fetch_ohlcv returns OHLCV data.
    
    Expected behavior when implemented:
    - Returns pandas DataFrame
    - Contains OHLCV columns (provider-specific format, typically Korean column names)
    - Has datetime index
    - Data is sorted by date
    - Date range matches requested range
    """
    import pandas as pd
    
    composite = CompositeProvider()
    
    # Use a known ticker (Samsung Electronics)
    ticker = "005930"
    start_date = "2024-01-02"
    end_date = "2024-01-31"
    
    df = composite.fetch_ohlcv(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        adjusted=True,
    )
    
    assert isinstance(df, pd.DataFrame), "Should return pandas DataFrame"
    assert not df.empty, "OHLCV data should not be empty"
    
    # Check that index is datetime
    assert isinstance(df.index, pd.DatetimeIndex), "Index should be DatetimeIndex"
    
    # Check data is sorted by date
    assert df.index.is_monotonic_increasing, "Data should be sorted by date"
    
    # Verify date range
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    assert df.index[0].date() >= start_dt.date(), "Start date should be within range"
    assert df.index[-1].date() <= end_dt.date(), "End date should be within range"

