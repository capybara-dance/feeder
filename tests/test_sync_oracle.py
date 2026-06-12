from __future__ import annotations

import datetime as dt
import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path

import pandas as pd

from scripts.dotenv_loader import load_dotenv_if_present


def test_load_dotenv_reads_values_and_preserves_existing_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "KRX_ID=test-user\nKRX_PW=test-pass\nEXISTING=from-file\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("EXISTING", "kept")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)

    load_dotenv_if_present(env_path)

    assert os.environ["KRX_ID"] == "test-user"
    assert os.environ["KRX_PW"] == "test-pass"
    assert os.environ["EXISTING"] == "kept"


def test_pykrx_provider_module_import_is_lazy():
    module_path = Path(__file__).resolve().parents[1] / "capybara_fetcher" / "providers" / "pykrx_provider.py"
    spec = importlib.util.spec_from_file_location("pykrx_provider_lazy_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert hasattr(module, "PykrxProvider")


def test_pykrx_import_failure_is_cached(monkeypatch):
    module_path = Path(__file__).resolve().parents[1] / "capybara_fetcher" / "providers" / "pykrx_provider.py"
    spec = importlib.util.spec_from_file_location("pykrx_provider_cache_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    calls: list[str] = []

    def fake_import(name: str):
        calls.append(name)
        raise RuntimeError("boom")

    monkeypatch.setattr(module.importlib, "import_module", fake_import)
    module._PYKRX_STOCK_MODULE = None
    module._PYKRX_STOCK_IMPORT_ERROR = None

    try:
        module._get_stock_module()
    except RuntimeError:
        pass

    try:
        module._get_stock_module()
    except RuntimeError:
        pass

    assert calls == ["pykrx.stock"]


def test_composite_provider_stops_retrying_pykrx_after_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", types.ModuleType("yfinance"))

    composite_module = importlib.import_module("capybara_fetcher.providers.composite_provider")

    provider = composite_module.CompositeProvider()

    class FailingPykrx:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_ohlcv(self, **kwargs):
            self.calls += 1
            raise RuntimeError("pykrx down")

        def fetch_market_cap(self, **kwargs):
            raise RuntimeError("pykrx down")

    class FallbackFdr:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_ohlcv(self, **kwargs):
            self.calls += 1
            return pd.DataFrame({"시가": [1]})

    failing_pykrx = FailingPykrx()
    fallback_fdr = FallbackFdr()
    object.__setattr__(provider, "_pykrx_provider", failing_pykrx)
    object.__setattr__(provider, "_fdr_provider", fallback_fdr)
    object.__setattr__(provider, "_pykrx_ohlcv_available", None)

    first = provider.fetch_ohlcv(ticker="005930", start_date="2026-06-01", end_date="2026-06-02")
    second = provider.fetch_ohlcv(ticker="005930", start_date="2026-06-01", end_date="2026-06-02")

    assert not first.empty
    assert not second.empty
    assert failing_pykrx.calls == 1
    assert fallback_fdr.calls == 2


def test_fdr_provider_does_not_fallback_to_naver_on_krx_failure(monkeypatch):
    module_path = Path(__file__).resolve().parents[1] / "capybara_fetcher" / "providers" / "fdr_provider.py"
    spec = importlib.util.spec_from_file_location("fdr_provider_fallback_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    calls: list[str] = []

    def fake_data_reader(symbol: str):
        calls.append(symbol)
        if symbol.startswith("KRX:"):
            raise RuntimeError("krx denied")
        return pd.DataFrame({"Open": [1], "High": [1], "Low": [1], "Close": [1], "Volume": [1]}, index=pd.to_datetime(["2026-06-01"]))

    monkeypatch.setattr(module.fdr, "DataReader", fake_data_reader)

    provider = module.FdrProvider(source="KRX")

    try:
        provider.fetch_ohlcv(ticker="005930", start_date="2026-06-01", end_date="2026-06-02")
    except RuntimeError as exc:
        assert str(exc) == "krx denied"

    assert calls == ["KRX:005930"]


def test_composite_provider_routes_alpha_tickers_away_from_pykrx(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", types.ModuleType("yfinance"))

    composite_module = importlib.import_module("capybara_fetcher.providers.composite_provider")
    provider = composite_module.CompositeProvider()

    class FailingPykrx:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_ohlcv(self, **kwargs):
            self.calls += 1
            raise RuntimeError("should not be called for alpha tickers")

        def fetch_market_cap(self, **kwargs):
            self.calls += 1
            raise RuntimeError("should not be called for alpha tickers")

    class TrackingFdr:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_ohlcv(self, **kwargs):
            self.calls += 1
            return pd.DataFrame({"시가": [1]})

    failing_pykrx = FailingPykrx()
    tracking_fdr = TrackingFdr()
    object.__setattr__(provider, "_pykrx_provider", failing_pykrx)
    object.__setattr__(provider, "_fdr_provider", tracking_fdr)

    frame = provider.fetch_ohlcv(ticker="0004G0", start_date="2026-06-01", end_date="2026-06-02")
    cap = provider.fetch_market_cap(ticker="0004G0", start_date="2026-06-01", end_date="2026-06-02")

    assert not frame.empty
    assert cap.empty
    assert failing_pykrx.calls == 0
    assert tracking_fdr.calls == 1


def test_pykrx_provider_fetch_ohlcv_bulk_aggregates_markets(monkeypatch):
    """fetch_ohlcv_bulk should call get_market_ohlcv_by_ticker for each market per day."""
    module_path = Path(__file__).resolve().parents[1] / "capybara_fetcher" / "providers" / "pykrx_provider.py"
    spec = importlib.util.spec_from_file_location("pykrx_provider_bulk_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    calls: list[tuple[str, str]] = []

    def fake_get_market_ohlcv_by_ticker(date_str: str, market: str = "KOSPI", adjusted: bool = True) -> pd.DataFrame:
        calls.append((date_str, market))
        return pd.DataFrame(
            {"시가": [100], "고가": [110], "저가": [90], "종가": [105], "거래량": [1000]},
            index=pd.Index(["005930"], name="티커"),
        )

    fake_stock = types.SimpleNamespace(
        get_market_ohlcv_by_ticker=fake_get_market_ohlcv_by_ticker,
    )
    module._PYKRX_STOCK_MODULE = fake_stock
    module._PYKRX_STOCK_IMPORT_ERROR = None

    provider = module.PykrxProvider()
    # 2026-06-01 is a Monday; 2026-06-02 is a Tuesday → 2 weekdays → 4 calls (2 days × 2 markets)
    result = provider.fetch_ohlcv_bulk(start_date="2026-06-01", end_date="2026-06-02")

    assert len(calls) == 4
    markets_called = {m for _, m in calls}
    assert markets_called == {"KOSPI", "KOSDAQ"}
    assert "Ticker" in result.columns
    assert "Date" in result.columns


def test_pykrx_provider_fetch_ohlcv_bulk_skips_weekends(monkeypatch):
    """fetch_ohlcv_bulk should not make API calls for weekend dates."""
    module_path = Path(__file__).resolve().parents[1] / "capybara_fetcher" / "providers" / "pykrx_provider.py"
    spec = importlib.util.spec_from_file_location("pykrx_provider_bulk_weekend_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    calls: list[str] = []

    def fake_get_market_ohlcv_by_ticker(date_str: str, market: str = "KOSPI", adjusted: bool = True) -> pd.DataFrame:
        calls.append(date_str)
        return pd.DataFrame()

    fake_stock = types.SimpleNamespace(get_market_ohlcv_by_ticker=fake_get_market_ohlcv_by_ticker)
    module._PYKRX_STOCK_MODULE = fake_stock
    module._PYKRX_STOCK_IMPORT_ERROR = None

    provider = module.PykrxProvider()
    # 2026-05-30 is Saturday, 2026-05-31 is Sunday → no calls expected
    provider.fetch_ohlcv_bulk(start_date="2026-05-30", end_date="2026-05-31")

    assert calls == []


def test_composite_provider_disable_pykrx_per_ticker(monkeypatch):
    """disable_pykrx_per_ticker should route subsequent per-ticker calls to FDR."""
    monkeypatch.setitem(sys.modules, "yfinance", types.ModuleType("yfinance"))

    composite_module = importlib.import_module("capybara_fetcher.providers.composite_provider")
    provider = composite_module.CompositeProvider()

    class TrackingPykrx:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_ohlcv(self, **kwargs):
            self.calls += 1
            return pd.DataFrame({"종가": [100]})

        def fetch_market_cap(self, **kwargs):
            self.calls += 1
            return pd.DataFrame()

    class TrackingFdr:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_ohlcv(self, **kwargs):
            self.calls += 1
            return pd.DataFrame({"시가": [1]})

    tracking_pykrx = TrackingPykrx()
    tracking_fdr = TrackingFdr()
    object.__setattr__(provider, "_pykrx_provider", tracking_pykrx)
    object.__setattr__(provider, "_fdr_provider", tracking_fdr)
    object.__setattr__(provider, "_pykrx_ohlcv_available", True)

    provider.disable_pykrx_per_ticker()

    provider.fetch_ohlcv(ticker="005930", start_date="2026-06-01", end_date="2026-06-02")
    provider.fetch_market_cap(ticker="005930", start_date="2026-06-01", end_date="2026-06-02")

    assert tracking_pykrx.calls == 0
    assert tracking_fdr.calls == 1  # only OHLCV falls back to fdr; market_cap returns empty


def test_composite_provider_bulk_ohlcv_returns_empty_on_pykrx_failure(monkeypatch):
    """fetch_ohlcv_bulk should return an empty DataFrame when pykrx raises."""
    monkeypatch.setitem(sys.modules, "yfinance", types.ModuleType("yfinance"))

    composite_module = importlib.import_module("capybara_fetcher.providers.composite_provider")
    provider = composite_module.CompositeProvider()

    class FailingPykrx:
        def fetch_ohlcv_bulk(self, **kwargs):
            raise RuntimeError("pykrx down")

        def fetch_market_cap_bulk(self, **kwargs):
            raise RuntimeError("pykrx down")

    object.__setattr__(provider, "_pykrx_provider", FailingPykrx())

    result = provider.fetch_ohlcv_bulk(start_date="2026-06-01", end_date="2026-06-02")
    assert result.empty

    result_cap = provider.fetch_market_cap_bulk(start_date="2026-06-01", end_date="2026-06-02")
    assert result_cap.empty


def test_collect_data_uses_bulk_ohlcv_and_skips_per_ticker_pykrx(monkeypatch):
    """collect_data should use bulk OHLCV data and not call per-ticker pykrx."""
    monkeypatch.setitem(sys.modules, "yfinance", types.ModuleType("yfinance"))

    collect_module = importlib.import_module("capybara_fetcher.pipeline.collect")
    composite_module = importlib.import_module("capybara_fetcher.providers.composite_provider")

    ticker = "005930"
    trade_date = pd.Timestamp("2026-06-02")

    bulk_ohlcv = pd.DataFrame({
        "Ticker": [ticker],
        "Date": [trade_date],
        "시가": [70000],
        "고가": [71000],
        "저가": [69000],
        "종가": [70500],
        "거래량": [500000],
    })
    bulk_cap = pd.DataFrame({
        "Ticker": [ticker],
        "Date": [trade_date],
        "시가총액": [420_000_000_000],
    })

    master_df = pd.DataFrame({
        "Code": [ticker],
        "Name": ["Samsung"],
        "Market": ["KOSPI"],
        "IndustryLarge": ["IT"],
        "IndustryMid": ["반도체"],
        "IndustrySmall": [""],
        "SharesOutstanding": [5_969_782_550],
    })

    per_ticker_pykrx_called = []

    class BulkProvider:
        name = "bulk_test"

        def load_stock_master(self, **kwargs):
            return master_df

        def list_tickers(self, **kwargs):
            return [ticker], {ticker: "KOSPI"}

        def fetch_ohlcv_bulk(self, **kwargs):
            return bulk_ohlcv.copy()

        def fetch_market_cap_bulk(self, **kwargs):
            return bulk_cap.copy()

        def disable_pykrx_per_ticker(self):
            pass

        def fetch_ohlcv(self, **kwargs):
            per_ticker_pykrx_called.append(kwargs.get("ticker"))
            return pd.DataFrame()

        def fetch_market_cap(self, **kwargs):
            per_ticker_pykrx_called.append(kwargs.get("ticker"))
            return pd.DataFrame()

        def fetch_market_cap_snapshot(self, **kwargs):
            return None

        def fetch_dividends(self, **kwargs):
            return pd.DataFrame(columns=["Date", "Dividend"])

    monkeypatch.setattr(collect_module, "CompositeProvider", lambda **kw: BulkProvider())

    cfg = collect_module.CollectionConfig(
        start_date="2026-06-02",
        end_date="2026-06-02",
        max_workers=1,
    )
    result = collect_module.collect_data(cfg)

    # bulk data was used: per-ticker provider.fetch_ohlcv was NOT called
    assert per_ticker_pykrx_called == []
    assert len(result.price_df) == 1
    assert result.price_df.iloc[0]["TICKER"] == ticker