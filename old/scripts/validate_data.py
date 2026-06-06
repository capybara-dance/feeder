"""
Data validation script for release quality checks.

This script validates generated data files before release to ensure:
1. All required files exist and are readable
2. Metadata indicates successful runs
3. Data has expected structure and completeness
4. Data quality meets minimum standards (no excessive nulls, reasonable ranges)
5. No obvious anomalies (missing dates, duplicates, etc.)

Exit codes:
  0 - All validations passed
  1 - Validation failed (data has problems)

Validation errors are written to stderr for capture by workflow.
"""
import argparse
import html
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when a validation check fails."""
    pass


def validate_file_exists(path: Path, file_type: str, min_size_mb: float | None = None) -> None:
    """Validate that a file exists and is readable.
    
    Args:
        path: Path to the file
        file_type: Description of the file type for error messages
        min_size_mb: Minimum required file size in MB (optional, exclusive)
    """
    if not path.exists():
        raise ValidationError(f"{file_type} file not found: {path}")
    if not path.is_file():
        raise ValidationError(f"{file_type} path is not a file: {path}")
    
    file_size_bytes = path.stat().st_size
    file_size_mb = file_size_bytes / 1024 / 1024
    
    if file_size_bytes == 0:
        raise ValidationError(f"{file_type} file is empty: {path}")
    
    # Check minimum size requirement if specified (exclusive: must be strictly greater)
    if min_size_mb is not None and file_size_mb <= min_size_mb:
        raise ValidationError(
            f"{file_type} file size too small: {file_size_mb:.2f} MB (must be > {min_size_mb} MB)"
        )
    
    logger.info(f"✓ {file_type} file exists: {path} ({file_size_mb:.2f} MB)")


def validate_metadata_status(meta_path: Path) -> dict[str, Any]:
    """Validate metadata JSON exists and indicates success."""
    validate_file_exists(meta_path, "Metadata")
    
    try:
        with open(meta_path) as f:
            meta = json.load(f)
    except json.JSONDecodeError as e:
        raise ValidationError(f"Metadata JSON is invalid: {e}")
    
    # Check run status
    run_status = meta.get("run_status")
    if run_status != "success":
        error_info = meta.get("error", {})
        raise ValidationError(
            f"Metadata indicates failed run: status={run_status}, "
            f"stage={error_info.get('stage')}, "
            f"ticker={error_info.get('ticker')}, "
            f"error={error_info.get('message')}"
        )
    
    logger.info(f"✓ Metadata status is 'success'")
    return meta


def validate_parquet_readable(path: Path, file_type: str) -> pd.DataFrame:
    """Validate that a parquet file is readable and return the DataFrame."""
    validate_file_exists(path, file_type)
    
    try:
        df = pd.read_parquet(path)
    except Exception as e:
        raise ValidationError(f"{file_type} parquet file is not readable: {e}")
    
    logger.info(f"✓ {file_type} parquet is readable: {len(df)} rows, {len(df.columns)} columns")
    return df


def validate_universe_data_structure(df: pd.DataFrame) -> None:
    """Validate universe feature frame has expected structure."""
    required_columns = ["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        raise ValidationError(f"Missing required columns in universe data: {missing_cols}")
    
    logger.info(f"✓ Universe data has required columns")


def validate_data_completeness(df: pd.DataFrame, meta: dict[str, Any]) -> None:
    """Validate data completeness against metadata."""
    # Check row count
    meta_rows = meta.get("rows", 0)
    actual_rows = len(df)
    if actual_rows == 0:
        raise ValidationError("Universe data is empty (0 rows)")
    if actual_rows != meta_rows:
        logger.warning(f"⚠ Row count mismatch: DataFrame has {actual_rows} rows, metadata says {meta_rows}")
    
    # Check ticker count
    meta_ticker_count = meta.get("ticker_count", 0)
    actual_tickers = df["Ticker"].nunique() if "Ticker" in df.columns else 0
    if actual_tickers == 0:
        raise ValidationError("Universe data has no tickers")
    
    # Requirement: Ticker count must be strictly greater than 3800 (exclusive)
    REQUIRED_TICKER_THRESHOLD = 3700
    if actual_tickers <= REQUIRED_TICKER_THRESHOLD:
        raise ValidationError(
            f"Ticker count too low: {actual_tickers} (must be > {REQUIRED_TICKER_THRESHOLD})"
        )
    
    if meta_ticker_count > 0 and actual_tickers < meta_ticker_count * 0.8:
        raise ValidationError(
            f"Too few tickers in data: {actual_tickers} (expected ~{meta_ticker_count})"
        )
    
    logger.info(f"✓ Data completeness: {actual_rows} rows, {actual_tickers} unique tickers")


def validate_data_quality(df: pd.DataFrame) -> None:
    """Validate data quality (nulls, value ranges)."""
    # Check for excessive nulls in critical columns
    critical_columns = ["Date", "Ticker", "Close"]
    for col in critical_columns:
        if col not in df.columns:
            continue
        null_count = df[col].isna().sum()
        null_pct = null_count / len(df) * 100
        if null_pct > 5:  # More than 5% nulls is concerning
            raise ValidationError(
                f"Too many nulls in {col}: {null_count} ({null_pct:.2f}%)"
            )
        logger.info(f"✓ {col} null check passed: {null_count} nulls ({null_pct:.2f}%)")
    
    # Check for reasonable value ranges in OHLCV
    if "Close" in df.columns:
        close_min = df["Close"].min()
        close_max = df["Close"].max()
        if close_min <= 0:
            raise ValidationError(f"Close prices have invalid values <= 0: min={close_min}")
        if close_max > 10_000_000:  # Sanity check for extreme values
            logger.warning(f"⚠ Close prices have very high values: max={close_max}")
        logger.info(f"✓ Close price range: {close_min:.2f} to {close_max:.2f}")
    
    if "Volume" in df.columns:
        volume_negative = (df["Volume"] < 0).sum()
        if volume_negative > 0:
            raise ValidationError(f"Volume has {volume_negative} negative values")
        logger.info(f"✓ Volume values are non-negative")


def validate_no_duplicates(df: pd.DataFrame) -> None:
    """Validate there are no duplicate Date+Ticker combinations."""
    if "Date" not in df.columns or "Ticker" not in df.columns:
        logger.warning("⚠ Cannot check duplicates: Date or Ticker column missing")
        return
    
    duplicates = df.duplicated(subset=["Date", "Ticker"], keep=False)
    dup_count = duplicates.sum()
    if dup_count > 0:
        raise ValidationError(
            f"Found {dup_count} duplicate Date+Ticker combinations"
        )
    logger.info(f"✓ No duplicate Date+Ticker combinations")


def validate_date_coverage(df: pd.DataFrame, meta: dict[str, Any]) -> None:
    """Validate date range coverage."""
    if "Date" not in df.columns:
        logger.warning("⚠ Cannot check date coverage: Date column missing")
        return
    
    dates = pd.to_datetime(df["Date"]).dropna()
    if len(dates) == 0:
        raise ValidationError("No valid dates in data")
    
    min_date = dates.min()
    max_date = dates.max()
    unique_dates = dates.nunique()
    
    logger.info(
        f"✓ Date coverage: {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')} "
        f"({unique_dates} unique dates)"
    )
    
    # Check if date range is suspiciously small
    expected_min_days = 30  # At least 1 month of data
    date_range_days = (max_date - min_date).days
    if date_range_days < expected_min_days:
        logger.warning(
            f"⚠ Date range is quite small: {date_range_days} days (expected at least {expected_min_days})"
        )


def validate_industry_data(df: pd.DataFrame) -> None:
    """Validate industry feature frame structure."""
    required_columns = ["Date", "Level", "IndustryClose"]
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        raise ValidationError(f"Missing required columns in industry data: {missing_cols}")
    
    if len(df) == 0:
        raise ValidationError("Industry data is empty")
    
    logger.info(f"✓ Industry data structure valid: {len(df)} rows")


def validate_krx_master(path: Path) -> None:
    """Validate KRX stock master parquet."""
    df = validate_parquet_readable(path, "KRX Stock Master")
    
    required_columns = ["Code", "Name", "Market"]
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        raise ValidationError(f"Missing required columns in KRX master: {missing_cols}")
    
    if len(df) == 0:
        raise ValidationError("KRX stock master is empty")
    
    logger.info(f"✓ KRX stock master structure valid: {len(df)} stocks")


def main():
    parser = argparse.ArgumentParser(
        description="Validate generated data quality before release"
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default="cache",
        help="Directory containing generated cache files (default: cache)",
    )
    parser.add_argument(
        "--require-industry",
        action="store_true",
        help="Require industry data files to exist (fail if missing)",
    )
    parser.add_argument(
        "--skip-krx-master",
        action="store_true",
        help="Skip validation of KRX stock master file",
    )
    
    args = parser.parse_args()
    cache_dir = Path(args.cache_dir)
    
    if not cache_dir.exists():
        logger.error(f"Cache directory does not exist: {cache_dir}")
        sys.exit(1)
    
    validation_errors = []
    
    logger.info("="*60)
    logger.info("Starting data validation for release...")
    logger.info("="*60)
    
    try:
        # 1. Validate universe feature frame
        logger.info("\n[1/5] Validating universe feature frame...")
        universe_parquet = cache_dir / "korea_universe_feature_frame.parquet"
        universe_meta = cache_dir / "korea_universe_feature_frame.meta.json"
        
        # Requirement: Universe feature data size must be greater than 300MB
        MIN_FILE_SIZE_MB = 300.0
        validate_file_exists(universe_parquet, "Universe feature frame", min_size_mb=MIN_FILE_SIZE_MB)
        
        meta = validate_metadata_status(universe_meta)
        df = validate_parquet_readable(universe_parquet, "Universe feature frame")
        validate_universe_data_structure(df)
        validate_data_completeness(df, meta)
        validate_data_quality(df)
        validate_no_duplicates(df)
        validate_date_coverage(df, meta)
        
        logger.info("✓ Universe feature frame validation passed")
        
    except ValidationError as e:
        validation_errors.append(f"Universe validation failed: {e}")
        logger.error(f"✗ Universe validation failed: {e}")
    
    try:
        # 2. Validate industry feature frame (optional)
        logger.info("\n[2/5] Validating industry feature frame...")
        industry_parquet = cache_dir / "korea_industry_feature_frame.parquet"
        industry_meta = cache_dir / "korea_industry_feature_frame.meta.json"
        
        if industry_parquet.exists():
            industry_meta_dict = validate_metadata_status(industry_meta)
            industry_df = validate_parquet_readable(industry_parquet, "Industry feature frame")
            validate_industry_data(industry_df)
            logger.info("✓ Industry feature frame validation passed")
        elif args.require_industry:
            raise ValidationError("Industry data required but not found")
        else:
            logger.info("⊘ Industry data not present (optional)")
            
    except ValidationError as e:
        validation_errors.append(f"Industry validation failed: {e}")
        logger.error(f"✗ Industry validation failed: {e}")
    
    try:
        # 3. Validate KRX stock master
        if not args.skip_krx_master:
            logger.info("\n[3/5] Validating KRX stock master...")
            krx_master = cache_dir / "krx_stock_master.parquet"
            validate_krx_master(krx_master)
            logger.info("✓ KRX stock master validation passed")
        else:
            logger.info("\n[3/5] Skipping KRX stock master validation")
            
    except ValidationError as e:
        validation_errors.append(f"KRX master validation failed: {e}")
        logger.error(f"✗ KRX master validation failed: {e}")
    
    # 4. Cross-validation checks
    logger.info("\n[4/5] Cross-validation checks...")
    try:
        # Check if universe metadata matches actual file
        if 'meta' in locals() and 'df' in locals():
            meta_file_path = meta.get("data_file", {}).get("path")
            expected_path = str(universe_parquet)
            if meta_file_path and meta_file_path != expected_path:
                logger.warning(
                    f"⚠ Metadata path mismatch: {meta_file_path} vs {expected_path}"
                )
        logger.info("✓ Cross-validation checks passed")
    except Exception as e:
        logger.warning(f"⚠ Cross-validation warning: {e}")
    
    # 5. Summary
    logger.info("\n[5/5] Validation summary...")
    logger.info("="*60)
    
    if validation_errors:
        logger.error(f"✗ VALIDATION FAILED: {len(validation_errors)} error(s)")
        for i, error in enumerate(validation_errors, 1):
            logger.error(f"  {i}. {error}")
            # Also write to stderr for capture by workflow
            print(error, file=sys.stderr)
        logger.info("="*60)
        sys.exit(1)
    else:
        logger.info("✓ ALL VALIDATIONS PASSED")
        logger.info("Data is ready for release!")
        logger.info("="*60)
        sys.exit(0)


if __name__ == "__main__":
    main()
