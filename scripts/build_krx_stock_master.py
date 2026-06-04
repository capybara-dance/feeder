import argparse
import json
import sys
import warnings
from pathlib import Path

import pandas as pd
import FinanceDataReader as fdr

# Add the parent directory to the path to import from capybara_fetcher
sys.path.insert(0, str(Path(__file__).parent.parent))

from capybara_fetcher.providers.fdr_provider import FdrProvider


def _read_master_xlsx(path: Path, market: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    # Normalize column names (strip whitespace)
    df.columns = [str(c).strip() for c in df.columns]

    required = ["종목코드", "종목명", "업종(대분류)", "업종(중분류)", "업종(소분류)", "발행주식수"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in {path.name}: {missing}")

    shares = (
        pd.to_numeric(
            df["발행주식수"]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.strip(),
            errors="coerce",
        )
        .round()
    )
    # Ensure JSON-serializable python ints (or None)
    shares_py = [int(x) if pd.notna(x) else None for x in shares.tolist()]

    out = pd.DataFrame(
        {
            "Code": df["종목코드"].astype(str).str.strip().str.zfill(6),
            "Name": df["종목명"].astype(str).str.strip(),
            "Market": market,
            "IndustryLarge": df["업종(대분류)"].astype(str).str.strip(),
            "IndustryMid": df["업종(중분류)"].astype(str).str.strip(),
            "IndustrySmall": df["업종(소분류)"].astype(str).str.strip(),
            "SharesOutstanding": shares_py,
        }
    )
    out = out.dropna(subset=["Code"]).drop_duplicates(subset=["Code", "Market"])
    return out


def _update_names_from_fdr(df: pd.DataFrame, market: str) -> pd.DataFrame:
    """
    Update stock names from FinanceDataReader to ensure accuracy.
    
    Args:
        df: DataFrame with stock data (must have 'Code' and 'Name' columns)
        market: Market name ('KOSPI' or 'KOSDAQ')
    
    Returns:
        DataFrame with updated names from FDR
    """
    try:
        # Fetch stock listing from FDR
        fdr_df = fdr.StockListing(market)
        
        if fdr_df.empty:
            warnings.warn(f"No data fetched from FDR for {market}")
            return df
        
        # Ensure Code column is properly formatted in FDR data
        fdr_df['Code'] = fdr_df['Code'].astype(str).str.strip().str.zfill(6)
        
        # Create a mapping of Code -> Name from FDR data
        fdr_name_map = dict(zip(fdr_df['Code'], fdr_df['Name']))
        
        # Update names in the dataframe using vectorized operations
        df = df.copy()
        original_count = len(df)
        
        # Count how many names will be updated before updating
        old_names = df['Name'].copy()
        new_names = df['Code'].map(fdr_name_map)
        
        # Only update where we have FDR data
        mask = new_names.notna()
        df.loc[mask, 'Name'] = new_names[mask]
        
        # Count how many names actually changed
        updated_count = (old_names != df['Name']).sum()
        
        print(f"Updated {updated_count} stock names from FDR for {market} (total: {original_count})")
        return df
        
    except Exception as e:
        warnings.warn(f"Failed to update names from FDR for {market}: {str(e)}")
        return df


def _fetch_etf_data(master_json_path: str) -> pd.DataFrame:
    """Fetch ETF data using FdrProvider.list_tickers()."""
    try:
        # Use FdrProvider's list_tickers with market='ETF' to fetch ETF data
        fdr_provider = FdrProvider(master_json_path=master_json_path, source="KRX")
        tickers, market_by_ticker = fdr_provider.list_tickers(market='ETF')
        
        if not tickers:
            warnings.warn("No ETF data fetched via FdrProvider")
            return pd.DataFrame()
        
        # We need to fetch the full ETF data with names
        # Since list_tickers only returns codes, we need to use the internal fetch
        df_etf = fdr.StockListing('ETF/KR')
        
        if df_etf.empty:
            warnings.warn("No ETF data fetched")
            return pd.DataFrame()
        
        # Map ETF columns to master format
        # ETF data has: Symbol, Name, and other fields
        etf_master = pd.DataFrame({
            'Code': df_etf['Symbol'].astype(str).str.strip().str.zfill(6),
            'Name': df_etf['Name'].astype(str).str.strip(),
            'Market': 'ETF',
            'IndustryLarge': None,
            'IndustryMid': None,
            'IndustrySmall': None,
            'SharesOutstanding': None,
        })
        
        etf_master = etf_master.dropna(subset=["Code"]).drop_duplicates(subset=["Code", "Market"])
        print(f"Fetched {len(etf_master)} ETF entries via FdrProvider")
        return etf_master
        
    except Exception as e:
        warnings.warn(f"Failed to fetch ETF data: {str(e)}")
        return pd.DataFrame()


def main() -> None:
    p = argparse.ArgumentParser(description="Build KRX stock master JSON from Seibro Excel files and FDR ETF data")
    p.add_argument("--kospi-xlsx", type=str, default="/workspace/data/kospi.xlsx")
    p.add_argument("--kosdaq-xlsx", type=str, default="/workspace/data/kosdaq.xlsx")
    p.add_argument("--output-json", type=str, default="/workspace/data/krx_stock_master.json")
    p.add_argument("--include-etf", action="store_true", default=True, help="Include ETF data from FinanceDataReader (default: True)")
    p.add_argument("--no-etf", dest="include_etf", action="store_false", help="Exclude ETF data")
    args = p.parse_args()

    kospi = _read_master_xlsx(Path(args.kospi_xlsx), market="KOSPI")
    kosdaq = _read_master_xlsx(Path(args.kosdaq_xlsx), market="KOSDAQ")
    
    # Update stock names from FDR to ensure accuracy
    kospi = _update_names_from_fdr(kospi, market="KOSPI")
    kosdaq = _update_names_from_fdr(kosdaq, market="KOSDAQ")

    master = pd.concat([kospi, kosdaq], ignore_index=True)
    
    # Fetch and add ETF data if requested
    if args.include_etf:
        etf_data = _fetch_etf_data(args.output_json)
        if not etf_data.empty:
            master = pd.concat([master, etf_data], ignore_index=True)
    
    master = master.sort_values(["Market", "Code"]).reset_index(drop=True)

    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records = master.to_dict(orient="records")
    out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {len(master)} rows -> {out_path}")
    print(f"  KOSPI: {len(kospi)}, KOSDAQ: {len(kosdaq)}, ETF: {len(master) - len(kospi) - len(kosdaq)}")


if __name__ == "__main__":
    main()

