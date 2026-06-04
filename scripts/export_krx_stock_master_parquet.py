import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> None:
    p = argparse.ArgumentParser(description="Export KRX stock master JSON to parquet")
    p.add_argument("--input-json", type=str, default="/workspace/data/krx_stock_master.json")
    p.add_argument("--output-parquet", type=str, default="/workspace/cache/krx_stock_master.parquet")
    args = p.parse_args()

    in_path = Path(args.input_json)
    if not in_path.exists():
        raise FileNotFoundError(f"Input JSON not found: {in_path}")

    data = json.loads(in_path.read_text(encoding="utf-8"))
    df = pd.DataFrame(data)

    # basic sanity / column order
    cols = ["Code", "Name", "Market", "IndustryLarge", "IndustryMid", "IndustrySmall", "SharesOutstanding"]
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[cols].copy()
    df["Code"] = df["Code"].astype(str).str.strip().str.zfill(6)
    df["SharesOutstanding"] = pd.to_numeric(df["SharesOutstanding"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["Code"]).drop_duplicates(subset=["Code", "Market"]).sort_values(["Market", "Code"])

    out_path = Path(args.output_parquet)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, compression="zstd")
    print(f"Wrote {len(df)} rows -> {out_path}")


if __name__ == "__main__":
    main()

