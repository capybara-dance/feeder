# Data Providers

This directory contains implementations of the `DataProvider` protocol for fetching Korean stock market data.

## Available Providers

### PykrxProvider

Uses the `pykrx` library to fetch OHLCV data.

- **Universe/Master**: Local JSON file (`data/krx_stock_master.json`)
- **OHLCV**: pykrx library
- **⚠️ Known API Issues**: Some pykrx APIs may experience temporary failures:
  - Ticker list APIs (KOSPI/KOSDAQ listing) may not work intermittently
  - ETF listing API may be unavailable at times
  - This is why we use the local `krx_stock_master.json` file (sourced from Seibro Excel) instead of relying on pykrx's ticker list functions
- **Usage**:
  ```python
  from capybara_fetcher.providers import PykrxProvider
  
  provider = PykrxProvider(
      master_json_path="data/krx_stock_master.json"
  )
  ```

### KoreaInvestmentProvider

Uses Korea Investment Securities Open Trading API to fetch OHLCV data.

- **Universe/Master**: Local JSON file (`data/krx_stock_master.json`)
- **OHLCV**: Korea Investment API
- **Requirements**: 
  - API Key (appkey) - stored in `HT_KE` repo secret
  - API Secret (appsecret) - stored in `HT_SE` repo secret
- **API Documentation**: https://github.com/koreainvestment/open-trading-api
- **Usage**:
  ```python
  from capybara_fetcher.providers import KoreaInvestmentProvider
  import os
  
  provider = KoreaInvestmentProvider(
      master_json_path="data/krx_stock_master.json",
      appkey=os.environ["HT_KE"],
      appsecret=os.environ["HT_SE"],
  )
  ```

### FdrProvider

Uses FinanceDataReader library to fetch OHLCV data.

- **Universe/Master**: Local JSON file (`data/krx_stock_master.json`)
- **OHLCV**: FinanceDataReader (FDR) library
- **Supported Sources**: 
  - `KRX`: Korean Exchange (default, provides historical data back to 1995)
  - `NAVER`: Naver Finance (data from 2000 onwards)
  - `YAHOO`: Yahoo Finance
- **Automatic Fallback**: When using KRX source, automatically falls back to NAVER for unsupported tickers (e.g., ETFs like 069500 KODEX 200)
- **Fetch All Data**: Fetches all available data using `fdr.DataReader(symbol)` without date parameters, then filters to the requested date range. This avoids API rate limits and threading issues.
- **Library Documentation**: https://github.com/FinanceData/FinanceDataReader
- **Installation**: `pip install finance-datareader`
- **⚠️ Multi-threading Warning**: FinanceDataReader is **NOT thread-safe** when fetching OHLCV data. When using `max_workers > 1` in the orchestrator, the 2-year API limit and other errors may occur. **Always use `max_workers=1` with FdrProvider** to avoid these issues.
- **Usage**:
  ```python
  from capybara_fetcher.providers import FdrProvider
  
  # Using KRX source (default)
  provider = FdrProvider(
      master_json_path="data/krx_stock_master.json",
      source="KRX",
  )
  
  # Using NAVER source
  provider = FdrProvider(
      master_json_path="data/krx_stock_master.json",
      source="NAVER",
  )
  ```

## DataProvider Protocol

All providers implement the `DataProvider` protocol defined in `provider.py`:

```python
class DataProvider(Protocol):
    name: str
    
    def list_tickers(
        self, *, asof_date: dt.date | None = None, market: str | None = None
    ) -> tuple[list[str], dict[str, str]]:
        """Returns tickers and market mapping."""
    
    def load_stock_master(
        self, *, asof_date: dt.date | None = None
    ) -> pd.DataFrame:
        """Returns stock master DataFrame."""
    
    def fetch_ohlcv(
        self, *, ticker: str, start_date: str, end_date: str, adjusted: bool = True
    ) -> pd.DataFrame:
        """Fetch OHLCV data for a ticker."""
```

## Adding a New Provider

To add a new data provider:

1. Create a new file in this directory (e.g., `new_provider.py`)
2. Implement the `DataProvider` protocol
3. Add the provider to `__init__.py` exports
4. Create tests in `tests/test_new_provider.py`
5. Update this README

## Testing

Tests are located in the `tests/` directory. External API tests are marked with `@pytest.mark.external` and can be run with:

```bash
# Run non-external tests only
pytest tests/ -m "not external"

# Run all tests including external API calls
pytest tests/
```
