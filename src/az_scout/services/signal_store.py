"""Time-series storage for SKU signals.

Stores periodic snapshots of SKU-level signals (spot score, pricing,
zones, restrictions, confidence) in an in-process SQLite database.
The database is created automatically on first use and lives at
``$AZ_SCOUT_DATA_DIR/signals.db`` (default: ``~/.az-scout/signals.db``).

This data is **collected by this tool** and is *not* an Azure internal
data source.  All derived metrics are heuristic estimates.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DATA_DIR = Path(os.environ.get("AZ_SCOUT_DATA_DIR", Path.home() / ".az-scout"))
_DB_PATH = _DATA_DIR / "signals.db"

# Thread-local database connections (SQLite is not thread-safe by default)
_local = threading.local()


def _ensure_data_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection, creating the DB if needed."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is None:
        _ensure_data_dir()
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
        _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create the ``sku_signals_timeseries`` table if it does not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sku_signals_timeseries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            region TEXT NOT NULL,
            sku TEXT NOT NULL,
            spot_score TEXT,
            paygo_price REAL,
            spot_price REAL,
            zones_supported_count INTEGER,
            restrictions_present INTEGER,
            confidence_score INTEGER,
            data_source_version TEXT DEFAULT '1'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_signals_region_sku_ts
        ON sku_signals_timeseries (region, sku, timestamp)
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SignalRow(TypedDict):
    timestamp: str
    region: str
    sku: str
    spot_score: str | None
    paygo_price: float | None
    spot_price: float | None
    zones_supported_count: int | None
    restrictions_present: bool | None
    confidence_score: int | None
    data_source_version: str


# ---------------------------------------------------------------------------
# Write API
# ---------------------------------------------------------------------------


def record_signal(
    *,
    region: str,
    sku: str,
    spot_score: str | None = None,
    paygo_price: float | None = None,
    spot_price: float | None = None,
    zones_supported_count: int | None = None,
    restrictions_present: bool | None = None,
    confidence_score: int | None = None,
    data_source_version: str = "1",
) -> None:
    """Insert a single signal snapshot into the time-series store."""
    conn = _get_conn()
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO sku_signals_timeseries
            (timestamp, region, sku, spot_score, paygo_price, spot_price,
             zones_supported_count, restrictions_present, confidence_score,
             data_source_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            region,
            sku,
            spot_score,
            paygo_price,
            spot_price,
            zones_supported_count,
            1 if restrictions_present else (0 if restrictions_present is not None else None),
            confidence_score,
            data_source_version,
        ),
    )
    conn.commit()


def record_signals_batch(rows: list[dict]) -> None:
    """Insert multiple signal snapshots in a single transaction."""
    conn = _get_conn()
    now = datetime.now(UTC).isoformat()
    conn.executemany(
        """
        INSERT INTO sku_signals_timeseries
            (timestamp, region, sku, spot_score, paygo_price, spot_price,
             zones_supported_count, restrictions_present, confidence_score,
             data_source_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                now,
                r["region"],
                r["sku"],
                r.get("spot_score"),
                r.get("paygo_price"),
                r.get("spot_price"),
                r.get("zones_supported_count"),
                1
                if r.get("restrictions_present")
                else (0 if r.get("restrictions_present") is not None else None),
                r.get("confidence_score"),
                r.get("data_source_version", "1"),
            )
            for r in rows
        ],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def get_signals(
    region: str,
    sku: str,
    *,
    hours: int = 24,
    limit: int = 1000,
) -> list[SignalRow]:
    """Return recent signal rows for a (region, sku) pair.

    Retrieves rows from the last *hours* hours, newest first, limited to
    *limit* rows.
    """
    conn = _get_conn()
    cutoff = datetime.now(UTC).isoformat()
    # SQLite ISO comparison works because timestamps are ISO-8601.
    cursor = conn.execute(
        """
        SELECT timestamp, region, sku, spot_score, paygo_price, spot_price,
               zones_supported_count, restrictions_present, confidence_score,
               data_source_version
        FROM sku_signals_timeseries
        WHERE region = ? AND sku = ?
          AND timestamp >= datetime(?, '-' || ? || ' hours')
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (region, sku, cutoff, hours, limit),
    )
    rows: list[SignalRow] = []
    for r in cursor.fetchall():
        rows.append(
            SignalRow(
                timestamp=r["timestamp"],
                region=r["region"],
                sku=r["sku"],
                spot_score=r["spot_score"],
                paygo_price=r["paygo_price"],
                spot_price=r["spot_price"],
                zones_supported_count=r["zones_supported_count"],
                restrictions_present=bool(r["restrictions_present"])
                if r["restrictions_present"] is not None
                else None,
                confidence_score=r["confidence_score"],
                data_source_version=r["data_source_version"] or "1",
            )
        )
    return rows


def get_signal_count(region: str, sku: str, *, hours: int = 24) -> int:
    """Return the number of signal rows in the given window."""
    conn = _get_conn()
    cutoff = datetime.now(UTC).isoformat()
    cursor = conn.execute(
        """
        SELECT COUNT(*) FROM sku_signals_timeseries
        WHERE region = ? AND sku = ?
          AND timestamp >= datetime(?, '-' || ? || ' hours')
        """,
        (region, sku, cutoff, hours),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def purge_old_signals(*, keep_days: int = 30) -> int:
    """Delete signal rows older than *keep_days* days.  Returns count deleted."""
    conn = _get_conn()
    cutoff = datetime.now(UTC).isoformat()
    cursor = conn.execute(
        """
        DELETE FROM sku_signals_timeseries
        WHERE timestamp < datetime(?, '-' || ? || ' days')
        """,
        (cutoff, keep_days),
    )
    conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Testing helper
# ---------------------------------------------------------------------------

_test_db: str | None = None


def use_memory_db() -> None:
    """Switch to an in-memory SQLite DB for testing.  Not thread-safe."""
    global _test_db  # noqa: PLW0603
    _test_db = ":memory:"
    conn = sqlite3.connect(_test_db, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    _local.conn = conn


def reset_test_db() -> None:
    """Close the in-memory DB and reset state."""
    global _test_db  # noqa: PLW0603
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None
    _test_db = None
