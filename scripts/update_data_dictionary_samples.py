from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Ensure repository root import path when run as script.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from capybara_fetcher.pipeline import CollectionConfig, collect_data

START_MARKER = "<!-- AUTO-SAMPLES-START -->"
END_MARKER = "<!-- AUTO-SAMPLES-END -->"


def _to_iso_date(v: str) -> str:
    s = str(v).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    dt.date.fromisoformat(s)
    return s


def _json_block(obj: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(obj, ensure_ascii=False, indent=2) + "\n```"


def _normalize_value(v: Any) -> Any:
    # Convert pandas/numpy types to plain JSON-serializable values.
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            pass
    return v


def _row_to_dict(row, keys: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in keys:
        out[k] = _normalize_value(row.get(k))
    return out


def _to_asset_type_label(v: Any) -> str:
    code = str(v).strip().upper()
    if code == "S":
        return "STOCK"
    if code == "E":
        return "ETF"
    if code == "N":
        return "ETN"
    return code or "UNKNOWN"


def _to_listed_label(v: Any) -> str:
    code = str(v).strip().upper()
    if code == "Y":
        return "LISTED"
    if code == "N":
        return "DELISTED"
    return code or "UNKNOWN"


def _pick_master_row(master_df):
    # Prefer a well-known representative ticker if available.
    preferred = ["005930", "000660", "035420"]
    for t in preferred:
        one = master_df[master_df["TICKER"] == t]
        if not one.empty:
            return one.iloc[0]
    return master_df.iloc[0]


def _pick_industry_row(industry_df):
    keys = ["LARGE_CLASS", "MEDIUM_CLASS", "SMALL_CLASS"]
    for _, row in industry_df.iterrows():
        if any(str(row.get(k, "")).strip() for k in keys):
            return row
    return industry_df.iloc[0]


def _pick_price_row(price_df, ticker: str | None = None):
    df = price_df
    if ticker:
        one = df[df["TICKER"] == ticker]
        if not one.empty:
            df = one
    nonzero = df[df["MARKET_CAP"] > 0]
    if not nonzero.empty:
        return nonzero.sort_values("PRICE_DATE", ascending=False).iloc[0]
    return df.sort_values("PRICE_DATE", ascending=False).iloc[0]


def _build_samples_section(result) -> str:
    industry_row = _pick_industry_row(result.industry_df) if not result.industry_df.empty else {}

    master_row = _pick_master_row(result.master_df) if not result.master_df.empty else {}
    master_ticker = master_row.get("TICKER") if hasattr(master_row, "get") else None

    price_row = _pick_price_row(result.price_df, ticker=master_ticker) if not result.price_df.empty else {}

    industry_sample = _row_to_dict(industry_row, ["INDUSTRY_CODE", "LARGE_CLASS", "MEDIUM_CLASS", "SMALL_CLASS"]) if hasattr(industry_row, "get") else {}
    master_sample = _row_to_dict(
        master_row,
        ["TICKER", "STOCK_NAME", "MARKET_CODE", "ASSET_TYPE", "INDUSTRY_CODE", "IS_LISTED", "UPDATED_AT"],
    ) if hasattr(master_row, "get") else {}
    if master_sample:
        master_sample["ASSET_TYPE"] = _to_asset_type_label(master_sample.get("ASSET_TYPE"))
        master_sample["IS_LISTED"] = _to_listed_label(master_sample.get("IS_LISTED"))
    price_sample = _row_to_dict(
        price_row,
        [
            "TICKER",
            "PRICE_DATE",
            "OPEN_PRICE",
            "HIGH_PRICE",
            "LOW_PRICE",
            "CLOSE_PRICE",
            "ADJ_CLOSE",
            "VOLUME",
            "MARKET_CAP",
            "RS_1M",
            "RS_3M",
            "RS_6M",
            "RS_12M",
            "RS_WEIGHTED",
        ],
    ) if hasattr(price_row, "get") else {}

    quality = {k: _normalize_value(v) for k, v in (result.quality_metrics or {}).items()}

    lines = [
        START_MARKER,
        "",
        "### 4.1 industry_df sample",
        "",
        _json_block(industry_sample),
        "",
        "### 4.2 master_df sample",
        "",
        _json_block(master_sample),
        "",
        "### 4.3 price_df sample",
        "",
        _json_block(price_sample),
        "",
        "### 4.4 quality_metrics sample",
        "",
        _json_block(quality),
        "",
        END_MARKER,
    ]
    return "\n".join(lines)


def update_document(doc_path: Path, sample_block: str) -> None:
    text = doc_path.read_text(encoding="utf-8")

    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        flags=re.DOTALL,
    )
    if not pattern.search(text):
        raise RuntimeError(f"Could not find sample markers in {doc_path}")

    updated = pattern.sub(sample_block, text)
    doc_path.write_text(updated, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Update docs/data_dictionary.md sample records from latest collection output")
    parser.add_argument("--doc", type=str, default="docs/data_dictionary.md")
    parser.add_argument("--start-date", type=str, default=(dt.date.today() - dt.timedelta(days=365)).strftime("%Y%m%d"))
    parser.add_argument("--end-date", type=str, default=dt.date.today().strftime("%Y%m%d"))
    parser.add_argument("--test-limit", type=int, default=10)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--market", type=str, default=None)
    args = parser.parse_args()

    cfg = CollectionConfig(
        start_date=_to_iso_date(args.start_date),
        end_date=_to_iso_date(args.end_date),
        test_limit=int(args.test_limit),
        max_workers=int(args.max_workers),
        adjusted=True,
        market=args.market,
        master_json_path=None,
    )

    result = collect_data(cfg)
    sample_block = _build_samples_section(result)
    doc_path = Path(args.doc)
    update_document(doc_path, sample_block)
    print(f"Updated sample section in {doc_path}")


if __name__ == "__main__":
    main()
