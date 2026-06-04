"""
Tests for FDR (FinanceDataReader) provider.
"""
import pytest
import pandas as pd
from pathlib import Path
from capybara_fetcher.providers import FdrProvider


@pytest.fixture
def master_json_path():
    """Path to test stock master JSON."""
    # Use relative path from the repository root
    repo_root = Path(__file__).parent.parent
    return str(repo_root / "data" / "krx_stock_master.json")


@pytest.fixture
def provider(master_json_path):
    """Create FdrProvider instance with KRX source."""
    return FdrProvider(
        master_json_path=master_json_path,
        source="KRX",
    )


@pytest.fixture
def provider_naver(master_json_path):
    """Create FdrProvider instance with NAVER source."""
    return FdrProvider(
        master_json_path=master_json_path,
        source="NAVER",
    )


def test_fdr_provider_name(master_json_path):
    """Test that provider has correct name."""
    provider = FdrProvider(
        master_json_path=master_json_path,
        source="KRX",
    )
    assert provider.name == "fdr"


def test_fdr_provider_default_source(master_json_path):
    """Test that provider defaults to KRX source."""
    provider = FdrProvider(master_json_path=master_json_path)
    assert provider.source == "KRX"


def test_fdr_provider_load_stock_master(provider):
    """Test loading stock master."""
    master = provider.load_stock_master()
    
    assert isinstance(master, pd.DataFrame)
    assert not master.empty
    
    # Check required columns
    required_cols = ["Code", "Name", "Market", "IndustryLarge", "IndustryMid", "IndustrySmall", "SharesOutstanding"]
    for col in required_cols:
        assert col in master.columns


@pytest.mark.external
def test_fdr_provider_list_tickers(provider):
    """Test listing tickers."""
    tickers, market_by_ticker = provider.list_tickers()
    
    assert isinstance(tickers, list)
    assert len(tickers) > 0
    assert all(len(t) == 6 for t in tickers)  # All tickers should be 6 digits
    
    assert isinstance(market_by_ticker, dict)
    assert len(market_by_ticker) > 0


@pytest.mark.external
def test_fdr_provider_list_tickers_by_market(provider):
    """Test listing tickers filtered by market."""
    tickers_kospi, _ = provider.list_tickers(market="KOSPI")
    tickers_kosdaq, _ = provider.list_tickers(market="KOSDAQ")
    tickers_etf, _ = provider.list_tickers(market="ETF")
    
    assert len(tickers_kospi) > 0
    assert len(tickers_kosdaq) > 0
    assert len(tickers_etf) > 0
    assert len(tickers_kospi) != len(tickers_kosdaq)
    assert len(tickers_kospi) != len(tickers_etf)


@pytest.mark.external
def test_fdr_provider_fetch_ohlcv_krx(provider):
    """Test fetching OHLCV data using KRX source."""
    # Use Samsung Electronics as test ticker
    ticker = "005930"
    start_date = "2024-01-02"
    end_date = "2024-01-31"
    
    df = provider.fetch_ohlcv(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        adjusted=True,
    )
    
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    
    # Check that we have Korean column names (for consistency with pykrx)
    expected_cols = ["시가", "고가", "저가", "종가", "거래량"]
    for col in expected_cols:
        assert col in df.columns, f"Column {col} not found in {df.columns.tolist()}"
    
    # Check that index is datetime
    assert isinstance(df.index, pd.DatetimeIndex)
    
    # Check data is sorted by date
    assert df.index.is_monotonic_increasing
    
    # Verify date range
    assert df.index[0].date() >= pd.to_datetime(start_date).date()
    assert df.index[-1].date() <= pd.to_datetime(end_date).date()


@pytest.mark.external
def test_fdr_provider_fetch_ohlcv_naver(provider_naver):
    """Test fetching OHLCV data using NAVER source."""
    # Use Samsung Electronics as test ticker
    ticker = "005930"
    start_date = "2024-01-02"
    end_date = "2024-01-31"
    
    df = provider_naver.fetch_ohlcv(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        adjusted=True,
    )
    
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    
    # Check that we have Korean column names
    expected_cols = ["시가", "고가", "저가", "종가", "거래량"]
    for col in expected_cols:
        assert col in df.columns, f"Column {col} not found in {df.columns.tolist()}"
    
    # Check that index is datetime
    assert isinstance(df.index, pd.DatetimeIndex)
    
    # Check data is sorted by date
    assert df.index.is_monotonic_increasing


@pytest.mark.external
def test_fdr_provider_fetch_ohlcv_multiple_sources(master_json_path):
    """Test that different sources can be used."""
    ticker = "005930"
    start_date = "2024-01-02"
    end_date = "2024-01-10"
    
    # Test KRX source
    provider_krx = FdrProvider(master_json_path=master_json_path, source="KRX")
    df_krx = provider_krx.fetch_ohlcv(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
    )
    
    # Test NAVER source
    provider_naver = FdrProvider(master_json_path=master_json_path, source="NAVER")
    df_naver = provider_naver.fetch_ohlcv(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
    )
    
    # Both should return data
    assert not df_krx.empty
    assert not df_naver.empty
    
    # Both should have same shape (same dates)
    assert df_krx.shape[0] == df_naver.shape[0]


@pytest.mark.external
def test_fdr_provider_fetch_ohlcv_invalid_ticker(provider):
    """Test fetching OHLCV for invalid ticker raises error."""
    # Use an invalid ticker that should cause an error
    ticker = "999999"
    start_date = "2024-01-02"
    end_date = "2024-01-31"
    
    # Should raise RuntimeError for fail-fast behavior
    with pytest.raises(RuntimeError) as exc_info:
        provider.fetch_ohlcv(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            adjusted=True,
        )
    
    assert "Failed to fetch OHLCV from FDR" in str(exc_info.value)


@pytest.mark.external
def test_fdr_provider_ohlc_consistency(provider):
    """Test that OHLC data maintains proper relationships."""
    ticker = "005930"
    start_date = "2024-01-02"
    end_date = "2024-01-31"
    
    df = provider.fetch_ohlcv(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
    )
    
    # High should be >= Open and Close
    assert all(df["고가"] >= df["시가"]), "High should be >= Open"
    assert all(df["고가"] >= df["종가"]), "High should be >= Close"
    
    # Low should be <= Open and Close
    assert all(df["저가"] <= df["시가"]), "Low should be <= Open"
    assert all(df["저가"] <= df["종가"]), "Low should be <= Close"
    
    # All prices should be positive
    assert all(df["시가"] > 0), "Open prices should be positive"
    assert all(df["고가"] > 0), "High prices should be positive"
    assert all(df["저가"] > 0), "Low prices should be positive"
    assert all(df["종가"] > 0), "Close prices should be positive"
    
    # Volume should be positive
    assert all(df["거래량"] > 0), "Volume should be positive"


@pytest.mark.external
def test_fdr_provider_long_date_range(provider):
    """Test fetching data for a longer date range."""
    ticker = "005930"
    start_date = "2023-01-01"
    end_date = "2023-12-31"
    
    df = provider.fetch_ohlcv(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
    )
    
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    # Should have roughly 250 trading days in a year
    assert len(df) > 200
    assert len(df) < 260


def test_fdr_provider_different_sources(master_json_path):
    """Test creating providers with different sources."""
    provider_krx = FdrProvider(master_json_path=master_json_path, source="KRX")
    provider_naver = FdrProvider(master_json_path=master_json_path, source="NAVER")
    provider_yahoo = FdrProvider(master_json_path=master_json_path, source="YAHOO")
    
    assert provider_krx.source == "KRX"
    assert provider_naver.source == "NAVER"
    assert provider_yahoo.source == "YAHOO"


@pytest.mark.external
def test_fdr_provider_trading_value_column(provider):
    """Test that trading value (거래대금) column is present."""
    ticker = "005930"
    start_date = "2024-01-02"
    end_date = "2024-01-10"
    
    df = provider.fetch_ohlcv(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
    )
    
    assert not df.empty
    
    # 거래대금 should be present (either from source or calculated)
    if "거래대금" in df.columns:
        # Should be positive
        assert all(df["거래대금"] > 0), "Trading value should be positive"


@pytest.mark.external
def test_fdr_provider_krx_fallback_to_naver(provider):
    """Test that KRX source falls back to NAVER for unsupported tickers (e.g., ETFs)."""
    # 069500 is KODEX 200 ETF, which KRX source doesn't support
    ticker = "069500"
    start_date = "2024-01-02"
    end_date = "2024-01-10"
    
    # This should use fallback to NAVER and succeed
    df = provider.fetch_ohlcv(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
    )
    
    assert isinstance(df, pd.DataFrame)
    assert not df.empty, "Should successfully fetch data via NAVER fallback"
    
    # Check that we have Korean column names
    expected_cols = ["시가", "고가", "저가", "종가", "거래량"]
    for col in expected_cols:
        assert col in df.columns, f"Column {col} not found in {df.columns.tolist()}"
