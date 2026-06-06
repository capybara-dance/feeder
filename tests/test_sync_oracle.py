from __future__ import annotations

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