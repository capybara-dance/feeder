"""
Tests for Korea Investment provider.
"""
import os
import pytest
import pandas as pd
from pathlib import Path
from capybara_fetcher.providers import KoreaInvestmentProvider


@pytest.fixture
def master_json_path():
    """Path to test stock master JSON."""
    # Use relative path from the repository root
    repo_root = Path(__file__).parent.parent
    return str(repo_root / "data" / "krx_stock_master.json")


@pytest.fixture
def provider_with_env(master_json_path):
    """Create provider instance using environment variables."""
    appkey = os.environ.get("HT_KE", "")
    appsecret = os.environ.get("HT_SE", "")
    
    if not appkey or not appsecret:
        pytest.skip("HT_KE and HT_SE environment variables not set")
    
    return KoreaInvestmentProvider(
        master_json_path=master_json_path,
        appkey=appkey,
        appsecret=appsecret,
    )


def test_korea_investment_provider_name(master_json_path):
    """Test that provider has correct name."""
    provider = KoreaInvestmentProvider(
        master_json_path=master_json_path,
        appkey="test_key",
        appsecret="test_secret",
    )
    assert provider.name == "korea_investment"


def test_korea_investment_auth_caching(master_json_path):
    """Test that auth instance is cached and reused."""
    provider = KoreaInvestmentProvider(
        master_json_path=master_json_path,
        appkey="test_key",
        appsecret="test_secret",
    )
    
    # Get auth instance twice
    auth1 = provider._get_auth()
    auth2 = provider._get_auth()
    
    # They should be the same instance (same object in memory)
    assert auth1 is auth2, "Auth instance should be cached and reused"
    
    # Verify the auth has correct credentials
    assert auth1.appkey == "test_key"
    assert auth1.appsecret == "test_secret"


def test_korea_investment_auth_reuse_across_multiple_calls(master_json_path):
    """Test that the same auth instance is used across multiple operations."""
    provider = KoreaInvestmentProvider(
        master_json_path=master_json_path,
        appkey="test_key",
        appsecret="test_secret",
    )
    
    # Simulate multiple operations that would call _get_auth
    auth_instances = []
    for _ in range(5):
        auth = provider._get_auth()
        auth_instances.append(auth)
    
    # All instances should be the same object
    first_auth = auth_instances[0]
    for auth in auth_instances[1:]:
        assert auth is first_auth, "All auth instances should be the same object"


def test_korea_investment_auth_thread_safety(master_json_path):
    """Test that auth caching is thread-safe."""
    import threading
    
    provider = KoreaInvestmentProvider(
        master_json_path=master_json_path,
        appkey="test_key",
        appsecret="test_secret",
    )
    
    auth_instances = []
    lock = threading.Lock()
    
    def get_auth_in_thread():
        auth = provider._get_auth()
        with lock:
            auth_instances.append(auth)
    
    # Create multiple threads that all try to get auth simultaneously
    threads = []
    for _ in range(10):
        t = threading.Thread(target=get_auth_in_thread)
        threads.append(t)
        t.start()
    
    # Wait for all threads to complete
    for t in threads:
        t.join()
    
    # All threads should have gotten the same auth instance
    assert len(auth_instances) == 10, "Should have 10 auth instances from 10 threads"
    first_auth = auth_instances[0]
    for auth in auth_instances[1:]:
        assert auth is first_auth, "All threads should get the same auth instance"


def test_korea_investment_load_stock_master(provider_with_env):
    """Test loading stock master."""
    master = provider_with_env.load_stock_master()
    
    assert isinstance(master, pd.DataFrame)
    assert not master.empty
    
    # Check required columns
    required_cols = ["Code", "Name", "Market", "IndustryLarge", "IndustryMid", "IndustrySmall", "SharesOutstanding"]
    for col in required_cols:
        assert col in master.columns


def test_korea_investment_list_tickers(provider_with_env):
    """Test listing tickers."""
    tickers, market_by_ticker = provider_with_env.list_tickers()
    
    assert isinstance(tickers, list)
    assert len(tickers) > 0
    assert all(len(t) == 6 for t in tickers)  # All tickers should be 6 digits
    
    assert isinstance(market_by_ticker, dict)
    assert len(market_by_ticker) > 0


def test_korea_investment_list_tickers_by_market(provider_with_env):
    """Test listing tickers filtered by market."""
    tickers_kospi, _ = provider_with_env.list_tickers(market="KOSPI")
    tickers_kosdaq, _ = provider_with_env.list_tickers(market="KOSDAQ")
    
    assert len(tickers_kospi) > 0
    assert len(tickers_kosdaq) > 0
    assert len(tickers_kospi) != len(tickers_kosdaq)


@pytest.mark.external
def test_korea_investment_fetch_ohlcv(provider_with_env):
    """Test fetching OHLCV data for a ticker."""
    # Use a stable ticker (Samsung Electronics)
    ticker = "005930"
    start_date = "2024-01-02"
    end_date = "2024-01-31"
    
    df = provider_with_env.fetch_ohlcv(
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


@pytest.mark.external
def test_korea_investment_fetch_ohlcv_empty_result(provider_with_env):
    """Test fetching OHLCV for invalid ticker returns empty DataFrame."""
    # Use an invalid ticker that should return no data
    ticker = "999999"
    start_date = "2024-01-02"
    end_date = "2024-01-31"
    
    try:
        df = provider_with_env.fetch_ohlcv(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            adjusted=True,
        )
        # If it doesn't raise an error, it should return empty DataFrame
        assert isinstance(df, pd.DataFrame)
    except RuntimeError:
        # It's acceptable to raise an error for invalid ticker
        pass
