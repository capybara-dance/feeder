from __future__ import annotations

import base64
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Any

import oracledb


@dataclass(frozen=True)
class OracleConnectionConfig:
    user: str
    password: str
    dsn: str
    wallet_b64: str | None = None
    wallet_password: str | None = None


class OracleClient:
    def __init__(
        self,
        *,
        config: OracleConnectionConfig,
        batch_size: int = 2000,
        commit_every_batches: int = 1,
    ) -> None:
        self._config = config
        self._batch_size = int(batch_size)
        self._commit_every_batches = max(1, int(commit_every_batches))
        self._conn: oracledb.Connection | None = None
        self._tmp_dir: tempfile.TemporaryDirectory[str] | None = None

    @staticmethod
    def from_env(*, batch_size: int = 2000) -> "OracleClient":
        user = os.getenv("OCI_DB_USER", "").strip()
        password = os.getenv("OCI_DB_PW", "").strip()
        dsn = os.getenv("OCI_DB_DSN", "").strip()
        wallet_b64 = os.getenv("OCI_WALLET", "").strip() or None
        wallet_password = os.getenv("OCI_WALLET_PW", "").strip() or None
        commit_every_batches = int(os.getenv("OCI_COMMIT_EVERY_BATCHES", "1").strip() or "1")

        missing = [k for k, v in {"OCI_DB_USER": user, "OCI_DB_PW": password, "OCI_DB_DSN": dsn}.items() if not v]
        if missing:
            raise ValueError(f"Missing Oracle env vars: {', '.join(missing)}")

        return OracleClient(
            config=OracleConnectionConfig(
                user=user,
                password=password,
                dsn=dsn,
                wallet_b64=wallet_b64,
                wallet_password=wallet_password,
            ),
            batch_size=batch_size,
            commit_every_batches=commit_every_batches,
        )

    def __enter__(self) -> "OracleClient":
        kwargs: dict[str, Any] = {
            "user": self._config.user,
            "password": self._config.password,
            "dsn": self._config.dsn,
        }

        if self._config.wallet_b64:
            self._tmp_dir = tempfile.TemporaryDirectory(prefix="oci_wallet_")
            wallet_dir = Path(self._tmp_dir.name)
            wallet_zip = wallet_dir / "wallet.zip"
            wallet_zip.write_bytes(base64.b64decode(self._config.wallet_b64))
            import zipfile

            with zipfile.ZipFile(wallet_zip, "r") as zf:
                zf.extractall(wallet_dir)

            kwargs["config_dir"] = str(wallet_dir)
            kwargs["wallet_location"] = str(wallet_dir)
            if self._config.wallet_password:
                kwargs["wallet_password"] = self._config.wallet_password

        self._conn = oracledb.connect(**kwargs)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._conn is not None:
                if exc is None:
                    self._conn.commit()
                else:
                    self._conn.rollback()
                self._conn.close()
        finally:
            if self._tmp_dir is not None:
                self._tmp_dir.cleanup()
                self._tmp_dir = None

    @property
    def connection(self) -> oracledb.Connection:
        if self._conn is None:
            raise RuntimeError("OracleClient is not connected. Use as context manager.")
        return self._conn

    def execute_many(self, sql: str, rows: list[Mapping[str, Any]]) -> int:
        if not rows:
            return 0

        total = 0
        batch_count = 0
        with self.connection.cursor() as cur:
            for i in range(0, len(rows), self._batch_size):
                batch = rows[i : i + self._batch_size]
                cur.executemany(sql, batch)
                total += len(batch)
                batch_count += 1
                # Commit frequently to keep UNDO usage bounded during large upsert runs.
                if batch_count % self._commit_every_batches == 0:
                    self.connection.commit()
        return total
