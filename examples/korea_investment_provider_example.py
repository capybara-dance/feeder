"""
Example: Using Korea Investment Provider

This example demonstrates how to use the KoreaInvestmentProvider
to fetch OHLCV data using Korea Investment Securities Open Trading API.
"""
import os
from capybara_fetcher.providers import KoreaInvestmentProvider

# Create provider instance
# In production, credentials should come from environment variables or repo secrets
provider = KoreaInvestmentProvider(
    master_json_path="data/krx_stock_master.json",
    appkey=os.environ.get("HT_KE", "your_app_key"),
    appsecret=os.environ.get("HT_SE", "your_app_secret"),
)

print(f"Provider: {provider.name}\n")

# 1. Load stock master data
print("=== Stock Master ===")
master = provider.load_stock_master()
print(f"Total stocks: {len(master)}")
print(f"Columns: {list(master.columns)}")
print(f"\nSample:\n{master.head()}\n")

# 2. List all tickers
print("=== All Tickers ===")
tickers, market_map = provider.list_tickers()
print(f"Total tickers: {len(tickers)}")
print(f"Sample tickers: {tickers[:10]}\n")

# 3. List tickers by market
print("=== Tickers by Market ===")
kospi_tickers, _ = provider.list_tickers(market="KOSPI")
kosdaq_tickers, _ = provider.list_tickers(market="KOSDAQ")
print(f"KOSPI: {len(kospi_tickers)} tickers")
print(f"KOSDAQ: {len(kosdaq_tickers)} tickers\n")

# 4. Fetch OHLCV data (requires valid API credentials)
print("=== Fetch OHLCV ===")
try:
    # Samsung Electronics (005930)
    ticker = "005930"
    start_date = "2024-01-02"
    end_date = "2024-01-31"
    
    df = provider.fetch_ohlcv(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        adjusted=True,
    )
    
    print(f"Ticker: {ticker}")
    print(f"Period: {start_date} to {end_date}")
    print(f"Records: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    print(f"\nSample data:\n{df.head()}")
    
except Exception as e:
    print(f"Note: OHLCV fetch requires valid API credentials")
    print(f"Error: {str(e)}")
