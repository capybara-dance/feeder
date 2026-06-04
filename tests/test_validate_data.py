"""Tests for data validation script."""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

# Import validation functions
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from validate_data import (
    ValidationError,
    validate_data_completeness,
    validate_file_exists,
    validate_metadata_status,
    validate_parquet_readable,
    validate_universe_data_structure,
    validate_data_quality,
    validate_no_duplicates,
    validate_krx_master,
    validate_industry_data,
)


def test_validate_file_exists_success(tmp_path):
    """Test file existence validation passes for valid file."""
    test_file = tmp_path / "test.parquet"
    test_file.write_text("dummy content")
    
    # Should not raise
    validate_file_exists(test_file, "Test file")


def test_validate_file_exists_with_min_size(tmp_path):
    """Test file size validation."""
    test_file = tmp_path / "test.parquet"
    # Create a file larger than 1MB
    test_file.write_bytes(b"x" * (2 * 1024 * 1024))
    
    # Should not raise for 1MB minimum
    validate_file_exists(test_file, "Test file", min_size_mb=1.0)
    
    # Should raise for 3MB minimum
    with pytest.raises(ValidationError, match="file size too small"):
        validate_file_exists(test_file, "Test file", min_size_mb=3.0)


def test_validate_file_exists_missing_file(tmp_path):
    """Test file existence validation fails for missing file."""
    test_file = tmp_path / "nonexistent.parquet"
    
    with pytest.raises(ValidationError, match="not found"):
        validate_file_exists(test_file, "Test file")


def test_validate_file_exists_empty_file(tmp_path):
    """Test file existence validation fails for empty file."""
    test_file = tmp_path / "empty.parquet"
    test_file.touch()
    
    with pytest.raises(ValidationError, match="empty"):
        validate_file_exists(test_file, "Test file")


def test_validate_metadata_status_success(tmp_path):
    """Test metadata validation passes for success status."""
    meta_file = tmp_path / "test.meta.json"
    meta = {"run_status": "success", "ticker_count": 100}
    meta_file.write_text(json.dumps(meta))
    
    result = validate_metadata_status(meta_file)
    assert result["run_status"] == "success"


def test_validate_metadata_status_failure(tmp_path):
    """Test metadata validation fails for failed status."""
    meta_file = tmp_path / "test.meta.json"
    meta = {
        "run_status": "failed",
        "error": {
            "stage": "fetch",
            "ticker": "005930",
            "message": "Network error"
        }
    }
    meta_file.write_text(json.dumps(meta))
    
    with pytest.raises(ValidationError, match="failed run"):
        validate_metadata_status(meta_file)


def test_validate_metadata_status_invalid_json(tmp_path):
    """Test metadata validation fails for invalid JSON."""
    meta_file = tmp_path / "test.meta.json"
    meta_file.write_text("not valid json {")
    
    with pytest.raises(ValidationError, match="invalid"):
        validate_metadata_status(meta_file)


def test_validate_parquet_readable(tmp_path):
    """Test parquet validation for readable file."""
    parquet_file = tmp_path / "test.parquet"
    df = pd.DataFrame({"A": [1, 2, 3], "B": [4, 5, 6]})
    df.to_parquet(parquet_file)
    
    result = validate_parquet_readable(parquet_file, "Test parquet")
    assert len(result) == 3


def test_validate_universe_data_structure_success():
    """Test universe data structure validation passes."""
    df = pd.DataFrame({
        "Date": ["2025-01-01"] * 3,
        "Ticker": ["005930", "000660", "035720"],
        "Open": [100, 200, 300],
        "High": [110, 210, 310],
        "Low": [90, 190, 290],
        "Close": [105, 205, 305],
        "Volume": [1000, 2000, 3000],
    })
    
    # Should not raise
    validate_universe_data_structure(df)


def test_validate_universe_data_structure_missing_column():
    """Test universe data structure validation fails for missing columns."""
    df = pd.DataFrame({
        "Date": ["2025-01-01"] * 3,
        "Ticker": ["005930", "000660", "035720"],
        # Missing OHLCV columns
    })
    
    with pytest.raises(ValidationError, match="Missing required columns"):
        validate_universe_data_structure(df)


def test_validate_data_completeness_success():
    """Test data completeness validation passes."""
    df = pd.DataFrame({
        "Date": ["2025-01-01"] * 4000,
        "Ticker": [f"{i:06d}" for i in range(4000)],
    })
    meta = {"rows": 4000, "ticker_count": 4000}
    
    # Should not raise (4000 tickers > 3800 threshold)
    validate_data_completeness(df, meta)


def test_validate_data_completeness_too_few_tickers():
    """Test data completeness validation fails for too few tickers."""
    df = pd.DataFrame({
        "Date": ["2025-01-01"] * 3800,
        "Ticker": [f"{i:06d}" for i in range(3800)],  # Exactly 3800 (fails since requirement is > 3800)
    })
    meta = {"rows": 3800, "ticker_count": 3800}
    
    with pytest.raises(ValidationError, match="Ticker count too low"):
        validate_data_completeness(df, meta)


def test_validate_data_completeness_empty():
    """Test data completeness validation fails for empty data."""
    df = pd.DataFrame()
    meta = {"rows": 0, "ticker_count": 0}
    
    with pytest.raises(ValidationError, match="empty"):
        validate_data_completeness(df, meta)


def test_validate_data_quality_success():
    """Test data quality validation passes."""
    df = pd.DataFrame({
        "Date": pd.date_range("2025-01-01", periods=100),
        "Ticker": ["005930"] * 100,
        "Close": range(100, 200),
        "Volume": [1000] * 100,
    })
    
    # Should not raise
    validate_data_quality(df)


def test_validate_data_quality_too_many_nulls():
    """Test data quality validation fails for excessive nulls."""
    df = pd.DataFrame({
        "Date": [None] * 90 + ["2025-01-01"] * 10,  # 90% nulls
        "Ticker": ["005930"] * 100,
        "Close": range(100, 200),
    })
    
    with pytest.raises(ValidationError, match="Too many nulls"):
        validate_data_quality(df)


def test_validate_data_quality_invalid_prices():
    """Test data quality validation fails for invalid price values."""
    df = pd.DataFrame({
        "Date": pd.date_range("2025-01-01", periods=100),
        "Ticker": ["005930"] * 100,
        "Close": [-10] + list(range(100, 199)),  # Negative price
        "Volume": [1000] * 100,
    })
    
    with pytest.raises(ValidationError, match="invalid values"):
        validate_data_quality(df)


def test_validate_data_quality_negative_volume():
    """Test data quality validation fails for negative volume."""
    df = pd.DataFrame({
        "Date": pd.date_range("2025-01-01", periods=100),
        "Ticker": ["005930"] * 100,
        "Close": range(100, 200),
        "Volume": [-1000] * 100,  # Negative volume
    })
    
    with pytest.raises(ValidationError, match="negative values"):
        validate_data_quality(df)


def test_validate_no_duplicates_success():
    """Test duplicate validation passes."""
    df = pd.DataFrame({
        "Date": ["2025-01-01", "2025-01-01", "2025-01-02", "2025-01-02"],
        "Ticker": ["005930", "000660", "005930", "000660"],
        "Close": [100, 200, 105, 210],
    })
    
    # Should not raise (no duplicates)
    validate_no_duplicates(df)


def test_validate_no_duplicates_failure():
    """Test duplicate validation fails."""
    df = pd.DataFrame({
        "Date": ["2025-01-01", "2025-01-01", "2025-01-01"],
        "Ticker": ["005930", "000660", "005930"],  # Duplicate Date+Ticker
        "Close": [100, 200, 105],
    })
    
    with pytest.raises(ValidationError, match="duplicate"):
        validate_no_duplicates(df)


def test_validate_krx_master_success(tmp_path):
    """Test KRX master validation passes."""
    master_file = tmp_path / "krx_stock_master.parquet"
    df = pd.DataFrame({
        "Code": ["005930", "000660", "035720"],
        "Name": ["삼성전자", "SK하이닉스", "카카오"],
        "Market": ["KOSPI", "KOSPI", "KOSDAQ"],
    })
    df.to_parquet(master_file)
    
    # Should not raise
    validate_krx_master(master_file)


def test_validate_krx_master_missing_columns(tmp_path):
    """Test KRX master validation fails for missing columns."""
    master_file = tmp_path / "krx_stock_master.parquet"
    df = pd.DataFrame({
        "Code": ["005930", "000660", "035720"],
        # Missing Name and Market columns
    })
    df.to_parquet(master_file)
    
    with pytest.raises(ValidationError, match="Missing required columns"):
        validate_krx_master(master_file)


def test_validate_industry_data_success():
    """Test industry data validation passes."""
    df = pd.DataFrame({
        "Date": ["2025-01-01"] * 3,
        "Level": ["large", "large", "mid"],
        "IndustryClose": [100.0, 105.0, 110.0],
    })
    
    # Should not raise
    validate_industry_data(df)


def test_validate_industry_data_missing_columns():
    """Test industry data validation fails for missing columns."""
    df = pd.DataFrame({
        "Date": ["2025-01-01"] * 3,
        # Missing Level and IndustryClose
    })
    
    with pytest.raises(ValidationError, match="Missing required columns"):
        validate_industry_data(df)


def test_validate_industry_data_empty():
    """Test industry data validation fails for empty data."""
    df = pd.DataFrame()
    
    with pytest.raises(ValidationError, match="Missing required columns"):
        validate_industry_data(df)


def test_validation_script_integration(tmp_path):
    """Test the full validation script end-to-end."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    
    # Create universe data with too few tickers (will fail validation)
    universe_df = pd.DataFrame({
        "Date": ["2025-01-01"] * 3500,  # Less than 3800 threshold
        "Ticker": [f"{i:06d}" for i in range(3500)],
        "Open": range(3500),
        "High": range(3500),
        "Low": range(3500),
        "Close": range(3500),
        "Volume": [1000] * 3500,
    })
    universe_file = cache_dir / "korea_universe_feature_frame.parquet"
    universe_df.to_parquet(universe_file)
    
    # Create metadata indicating success
    meta = {
        "run_status": "success",
        "ticker_count": 3500,
        "rows": 3500,
        "columns": ["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"],
    }
    meta_file = cache_dir / "korea_universe_feature_frame.meta.json"
    meta_file.write_text(json.dumps(meta))
    
    # Create valid KRX master
    master_df = pd.DataFrame({
        "Code": ["005930", "000660", "035720"],
        "Name": ["삼성전자", "SK하이닉스", "카카오"],
        "Market": ["KOSPI", "KOSPI", "KOSDAQ"],
    })
    master_file = cache_dir / "krx_stock_master.parquet"
    master_df.to_parquet(master_file)
    
    # Run validation script - should fail due to file size and ticker count
    script_path = Path(__file__).parent.parent / "scripts" / "validate_data.py"
    result = subprocess.run(
        [sys.executable, str(script_path), "--cache-dir", str(cache_dir), "--skip-krx-master"],
        capture_output=True,
        text=True,
    )
    
    # Should fail validation (file size < 300MB and ticker count <= 3800)
    assert result.returncode == 1, f"Expected validation to fail but got returncode {result.returncode}"
    # Check that stderr contains the expected error about file size or ticker count
    assert "file size too small" in result.stderr or "Ticker count too low" in result.stderr, \
        f"Expected validation error in stderr, got: {result.stderr}"
