"""
Example usage of FdrProvider (FinanceDataReader).

This script demonstrates how to use the FDR provider to fetch stock data.
"""
from capybara_fetcher.providers import FdrProvider

# Create FDR provider instance with default KRX source
provider = FdrProvider(
    master_json_path="data/krx_stock_master.json",
    source="KRX",  # Options: "KRX", "NAVER", "YAHOO"
)

# Get ticker list
print("Fetching ticker list...")
tickers, market_by_ticker = provider.list_tickers(market="KOSPI")
print(f"Found {len(tickers)} KOSPI stocks")
print(f"First 5 tickers: {tickers[:5]}")

# Fetch OHLCV data for Samsung Electronics (005930)
print("\nFetching OHLCV data for Samsung Electronics (005930)...")
ticker = "005930"
start_date = "2024-01-01"
end_date = "2024-01-31"

try:
    df = provider.fetch_ohlcv(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        adjusted=True,
    )
    
    print(f"\nData shape: {df.shape}")
    print(f"Columns: {df.columns.tolist()}")
    print(f"\nFirst 5 rows:")
    print(df.head())
    print(f"\nLast 5 rows:")
    print(df.tail())
    
except RuntimeError as e:
    print(f"Error fetching data: {e}")
    print("Note: This may fail in sandboxed environments without internet access.")

# Example with NAVER source
print("\n" + "="*60)
print("Using NAVER source instead of KRX:")
provider_naver = FdrProvider(
    master_json_path="data/krx_stock_master.json",
    source="NAVER",
)

try:
    df_naver = provider_naver.fetch_ohlcv(
        ticker="005930",
        start_date="2024-01-01",
        end_date="2024-01-10",
    )
    print(f"NAVER data shape: {df_naver.shape}")
    print(df_naver.head())
except RuntimeError as e:
    print(f"Error: {e}")
