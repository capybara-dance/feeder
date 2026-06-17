from __future__ import annotations

import datetime as dt
import sys

import pytest

from scripts import sync_daily_price_release


def test_sync_daily_price_release_includes_parent_tables(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_call(cmd: list[str]) -> int:
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(sync_daily_price_release.subprocess, "call", fake_call)
    monkeypatch.setattr(sync_daily_price_release, "_today_kst", lambda: dt.date(2026, 6, 17))
    monkeypatch.setattr(sys, "argv", ["sync_daily_price_release.py", "--lookback-days", "10"])

    with pytest.raises(SystemExit) as ex:
        sync_daily_price_release.main()

    assert ex.value.code == 0
    cmd = captured["cmd"]
    assert "--tables" in cmd
    assert cmd[cmd.index("--tables") + 1] == "industry,master,price"
