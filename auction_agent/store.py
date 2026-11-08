"""SQLite persistence: seen lots, decisions, watchlist, bid plans, sent alerts."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import Decision, Lot

SCHEMA = """
CREATE TABLE IF NOT EXISTS lots (
    key TEXT PRIMARY KEY, source TEXT, data TEXT, first_seen TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    key TEXT PRIMARY KEY, buy_box TEXT, data TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (
    key TEXT PRIMARY KEY, added TEXT, reason TEXT, max_bid REAL,
    bid_status TEXT DEFAULT 'suggested',   -- suggested | approved | placed | skipped
    approved_amount REAL, notes TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    key TEXT, kind TEXT, sent TEXT, PRIMARY KEY (key, kind)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path = "data/agent.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # lots ---------------------------------------------------------------
    def upsert_lot(self, lot: Lot) -> bool:
        """Insert or update a lot. Returns True if it's new."""
        row = self.db.execute("SELECT 1 FROM lots WHERE key=?", (lot.key,)).fetchone()
        data = lot.model_dump_json()
        if row:
            self.db.execute("UPDATE lots SET data=?, updated=? WHERE key=?", (data, _now(), lot.key))
        else:
            self.db.execute("INSERT INTO lots VALUES (?,?,?,?,?)",
                            (lot.key, lot.source, data, _now(), _now()))
        self.db.commit()
        return row is None

    def get_lot(self, key: str) -> Optional[Lot]:
        row = self.db.execute("SELECT data FROM lots WHERE key=?", (key,)).fetchone()
        return Lot.model_validate_json(row["data"]) if row else None

    # decisions ------------------------------------------------------------
    def has_decision(self, key: str, box_name: str) -> bool:
        return self.db.execute("SELECT 1 FROM decisions WHERE key=? AND buy_box=?",
                               (key, box_name)).fetchone() is not None

    def save_decision(self, d: Decision, box_name: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?)",
                        (d.lot_key, box_name, d.model_dump_json(), _now()))
        self.db.commit()

    def get_decision(self, key: str) -> Optional[Decision]:
        row = self.db.execute("SELECT data FROM decisions WHERE key=?", (key,)).fetchone()
        return Decision.model_validate_json(row["data"]) if row else None

    # watchlist ------------------------------------------------------------
    def watch(self, key: str, reason: str, max_bid: Optional[float]) -> None:
        self.db.execute("INSERT OR IGNORE INTO watchlist (key, added, reason, max_bid) VALUES (?,?,?,?)",
                        (key, _now(), reason, max_bid))
        self.db.commit()

    def unwatch(self, key: str) -> None:
        self.db.execute("DELETE FROM watchlist WHERE key=?", (key,))
        self.db.commit()

    def watchlist(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM watchlist ORDER BY added").fetchall()

    def set_bid(self, key: str, status: str, amount: Optional[float] = None) -> None:
        self.db.execute("UPDATE watchlist SET bid_status=?, approved_amount=COALESCE(?, approved_amount) WHERE key=?",
                        (status, amount, key))
        self.db.commit()

    # alerts ---------------------------------------------------------------
    def alert_sent(self, key: str, kind: str) -> bool:
        return self.db.execute("SELECT 1 FROM alerts WHERE key=? AND kind=?", (key, kind)).fetchone() is not None

    def mark_alert(self, key: str, kind: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO alerts VALUES (?,?,?)", (key, kind, _now()))
        self.db.commit()
