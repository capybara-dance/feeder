from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class MasterJsonProvider:
    name: str = "master_json"
    master_json_path: str | None = None

    def _resolve_master_json_path(self) -> Path:
        if self.master_json_path:
            p = Path(self.master_json_path)
            if p.exists():
                return p
            raise FileNotFoundError(f"master json not found: {p}")

        candidates = [
            Path("data/krx_stock_master.json"),
            Path("old/data/krx_stock_master.json"),
        ]
        for p in candidates:
            if p.exists():
                return p
        raise FileNotFoundError(f"krx_stock_master.json not found in: {[str(c) for c in candidates]}")

    def load_stock_master(self, *, asof_date: dt.date | None = None) -> pd.DataFrame:
        _ = asof_date
        p = self._resolve_master_json_path()
        data = json.loads(p.read_text(encoding="utf-8"))
        df = pd.DataFrame(data)
        if df.empty:
            raise ValueError(f"stock master is empty: {p}")

        for c in _MASTER_COLS:
            if c not in df.columns:
                df[c] = pd.NA

        master = df[_MASTER_COLS].copy()
        master["Code"] = master["Code"].astype(str).str.strip().str.zfill(6)
        master["Name"] = master["Name"].astype(str).str.strip()
        master["Market"] = master["Market"].astype(str).str.strip()

        for c in ["IndustryLarge", "IndustryMid", "IndustrySmall"]:
            master[c] = master[c].apply(lambda x: str(x).strip() if pd.notna(x) and x is not None else pd.NA)

        master["SharesOutstanding"] = pd.to_numeric(master["SharesOutstanding"], errors="coerce").astype("Int64")
        master = master.dropna(subset=["Code"]).drop_duplicates(subset=["Code", "Market"]).sort_values(["Market", "Code"])
        return master

    def list_tickers(
        self,
        *,
        asof_date: dt.date | None = None,
        market: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        master = self.load_stock_master(asof_date=asof_date)
        if market:
            m = str(market).strip()
            master = master[master["Market"] == m]
        tickers = sorted(master["Code"].astype(str).str.zfill(6).unique().tolist())
        market_by_ticker = dict(zip(master["Code"].tolist(), master["Market"].tolist()))
        return tickers, market_by_ticker
