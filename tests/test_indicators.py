import pandas as pd

from capybara_fetcher.indicators import compute_features, MA_WINDOWS, MRS_WINDOWS


def test_compute_features_adds_columns_and_new_high_flag():
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": range(1, 261),
            "High": range(1, 261),
            "Low": range(1, 261),
            "Close": range(1, 261),
            "Volume": [100] * 260,
            "TradingValue": [None] * 260,
            "Change": [None] * 260,
            "Ticker": ["000001"] * 260,
        }
    )
    bench = pd.Series([100.0] * 260, index=dates.normalize())

    out = compute_features(df, benchmark_close_by_date=bench)
    for w in MA_WINDOWS:
        assert f"SMA_{w}" in out.columns
    assert "MansfieldRS" in out.columns
    assert "IsNewHigh1Y" in out.columns
    assert "IsNewLow1Y" in out.columns
    # At the very end, close is increasing so last point should be new high (after 252 days)
    assert bool(out["IsNewHigh1Y"].iloc[-1]) is True
    # At the very end, low is increasing so last point should NOT be new low
    assert bool(out["IsNewLow1Y"].iloc[-1]) is False


def test_compute_features_handles_duplicate_benchmark_index():
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": range(1, 261),
            "High": range(1, 261),
            "Low": range(1, 261),
            "Close": range(1, 261),
            "Volume": [100] * 260,
            "TradingValue": [None] * 260,
            "Change": [None] * 260,
            "Ticker": ["000001"] * 260,
        }
    )
    # Duplicate benchmark index on purpose
    bench = pd.Series([100.0] * 260, index=dates.normalize())
    bench2 = pd.concat([bench, bench])  # duplicate dates

    out = compute_features(df, benchmark_close_by_date=bench2)
    assert "MansfieldRS" in out.columns


def test_compute_features_adds_mrs_raw_columns():
    """Test that multi-timeframe MRS raw columns are added."""
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": range(1, 261),
            "High": range(1, 261),
            "Low": range(1, 261),
            "Close": range(1, 261),
            "Volume": [100] * 260,
            "TradingValue": [None] * 260,
            "Change": [None] * 260,
            "Ticker": ["000001"] * 260,
        }
    )
    bench = pd.Series([100.0] * 260, index=dates.normalize())

    out = compute_features(df, benchmark_close_by_date=bench)
    
    # Check that all MRS raw columns are present
    for col_name in MRS_WINDOWS.keys():
        assert f"{col_name}_raw" in out.columns, f"{col_name}_raw not found"
    
    # Verify that MRS_12M_raw has valid values after 250 days
    assert out[f"MRS_12M_raw"].notna().sum() > 0, "MRS_12M_raw should have non-null values"


def test_mrs_percentile_conversion():
    """Test that percentile conversion works correctly across multiple stocks."""
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    
    # Create 3 stocks with different performance
    dfs = []
    for i, ticker in enumerate(["000001", "000002", "000003"]):
        df = pd.DataFrame(
            {
                "Date": dates,
                "Close": range(1 + i * 100, 261 + i * 100),  # Different price levels
                "Ticker": [ticker] * 260,
            }
        )
        dfs.append(df)
    
    combined = pd.concat(dfs, ignore_index=True)
    bench = pd.Series([100.0] * 260, index=dates.normalize())
    
    # Simulate adding raw MRS columns (using a simple value for testing)
    combined["MRS_1M_raw"] = combined["Close"] * 0.1  # Proportional to close
    
    # Calculate percentiles (same logic as in orchestrator)
    combined["MRS_1M"] = (
        combined.groupby("Date")["MRS_1M_raw"]
        .rank(pct=True, method="average")
        .mul(100.0)
        .round(2)
    )
    
    # Check that percentiles are in valid range
    assert combined["MRS_1M"].min() >= 0.0
    assert combined["MRS_1M"].max() <= 100.0
    
    # Check that highest performer gets highest percentile on each date
    for date in dates[:10]:  # Check first 10 dates
        date_data = combined[combined["Date"] == date].sort_values("MRS_1M_raw", ascending=False)
        if len(date_data) == 3:
            # Highest raw value should have highest percentile
            assert date_data.iloc[0]["MRS_1M"] == date_data["MRS_1M"].max()
            # Lowest raw value should have lowest percentile
            assert date_data.iloc[-1]["MRS_1M"] == date_data["MRS_1M"].min()


def test_new_low_1y_feature():
    """Test that IsNewLow1Y correctly identifies 52-week lows."""
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    
    # Create decreasing prices to test new low
    low_values = list(range(260, 0, -1))  # Decreasing from 260 to 1
    
    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": low_values,
            "High": low_values,
            "Low": low_values,
            "Close": low_values,
            "Volume": [100] * 260,
            "TradingValue": [None] * 260,
            "Change": [None] * 260,
            "Ticker": ["000001"] * 260,
        }
    )
    bench = pd.Series([100.0] * 260, index=dates.normalize())
    
    out = compute_features(df, benchmark_close_by_date=bench)
    
    # Check that IsNewLow1Y column exists
    assert "IsNewLow1Y" in out.columns
    
    # For decreasing prices, the last point should be a new low (after 252 days)
    assert bool(out["IsNewLow1Y"].iloc[-1]) is True
    
    # First point should NOT be a new low initially (not enough data)
    assert pd.isna(out["IsNewLow1Y"].iloc[0]) or bool(out["IsNewLow1Y"].iloc[0]) is False


def test_new_low_1y_with_constant_prices():
    """Test IsNewLow1Y with constant prices (all lows are equal)."""
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    
    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": [100] * 260,
            "High": [100] * 260,
            "Low": [100] * 260,
            "Close": [100] * 260,
            "Volume": [100] * 260,
            "TradingValue": [None] * 260,
            "Change": [None] * 260,
            "Ticker": ["000001"] * 260,
        }
    )
    bench = pd.Series([100.0] * 260, index=dates.normalize())
    
    out = compute_features(df, benchmark_close_by_date=bench)
    
    # With constant prices, all points after min_periods should be marked as new low
    # (since they all equal the rolling minimum)
    assert "IsNewLow1Y" in out.columns
    # After 252 days, all remaining values should be True (since all lows are equal)
    assert out["IsNewLow1Y"].iloc[252:].all()


