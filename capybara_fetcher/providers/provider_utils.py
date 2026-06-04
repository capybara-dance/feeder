"""
Common utility functions for data providers.
"""
from __future__ import annotations

import json
import pandas as pd


_MASTER_COLS = [
    "Code",
    "Name",
    "Market",
    "IndustryLarge",
    "IndustryMid",
    "IndustrySmall",
    "SharesOutstanding",
]


def load_master_json(path: str) -> pd.DataFrame:
    """
    Load stock master data from JSON file.
    
    This function is shared across providers that use local stock master files.
    
    Args:
        path: Path to JSON file containing stock master data
        
    Returns:
        DataFrame with standardized columns and types
        
    Raises:
        ValueError: If file is empty or has no valid rows
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    df = pd.DataFrame(data)
    if df.empty:
        raise ValueError(f"stock master is empty: {path}")

    for c in _MASTER_COLS:
        if c not in df.columns:
            df[c] = pd.NA

    out = df[_MASTER_COLS].copy()
    out["Code"] = out["Code"].astype(str).str.strip().str.zfill(6)
    out["Name"] = out["Name"].astype(str).str.strip()
    out["Market"] = out["Market"].astype(str).str.strip()
    out["IndustryLarge"] = out["IndustryLarge"].astype(str).str.strip()
    out["IndustryMid"] = out["IndustryMid"].astype(str).str.strip()
    out["IndustrySmall"] = out["IndustrySmall"].astype(str).str.strip()
    out["SharesOutstanding"] = pd.to_numeric(out["SharesOutstanding"], errors="coerce").astype("Int64")
    out = out.dropna(subset=["Code"]).drop_duplicates(subset=["Code", "Market"]).sort_values(["Market", "Code"])
    if out.empty:
        raise ValueError(f"stock master has no valid rows: {path}")
    return out
