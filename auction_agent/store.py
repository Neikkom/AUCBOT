"""SQLite persistence: seen lots, decisions, watchlist, bid plans, sent alerts.
Thread-safe: the dashboard and the background poller share one Store."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path = "data/agent.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self.db.executescript(SCHEMA)

    def _q(self, sql: str, params: tuple = (), one: bool = False) -> Any:
        with self._lock:
            cur = self.db.execute(sql, params)
            return cur.fetchone() if one else cur.fetchall()

    def _w(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self.db.execute(sql, params)
            self.db.commit()

    # lots ---------------------------------------------------------------
    def upsert_lot(self, lot: Lot) -> bool:
        """Insert or update a lot. Returns True if it's new."""
        with self._lock:
            exists = self._q("SELECT 1 FROM lots WHERE key=?", (lot.key,), one=True)
            if exists:
                self._w("UPDATE lots SET data=?, updated=? WHERE key=?", (lot.model_dump_json(), _now(), lot.key))
            else:
                self._w("INSERT INTO lots VALUES (?,?,?,?,?)",
                        (lot.key, lot.source, lot.model_dump_json(), _now(), _now()))
            return exists is None

    def get_lot(self, key: str) -> Optional[Lot]:
        row = self._q("SELECT data FROM lots WHERE key=?", (key,), one=True)
        return Lot.model_validate_json(row["data"]) if row else None

    def count_lots(self) -> int:
        return self._q("SELECT COUNT(*) n FROM lots", one=True)["n"]

    # decisions ------------------------------------------------------------
    def has_decision(self, key: str, box_name: str) -> bool:
        return self._q("SELECT 1 FROM decisions WHERE key=? AND buy_box=?", (key, box_name), one=True) is not None

    def save_decision(self, d: Decision, box_name: str) -> None:
        self._w("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?)",
                (d.lot_key, box_name, d.model_dump_json(), _now()))

    def get_decision(self, key: str) -> Optional[Decision]:
        row = self._q("SELECT data FROM decisions WHERE key=?", (key,), one=True)
        return Decision.model_validate_json(row["data"]) if row else None

    def matches(self, limit: int = 200) -> list[tuple[Lot, Decision, str]]:
        """Lots that passed the hard filters, newest first."""
        rows = self._q("""SELECT l.data ld, d.data dd, d.created FROM decisions d JOIN lots l ON l.key=d.key
                          ORDER BY d.created DESC LIMIT ?""", (limit * 3,))
        out = []
        for r in rows:
            d = Decision.model_validate_json(r["dd"])
            if d.passed_filters and d.evaluation:
                out.append((Lot.model_validate_json(r["ld"]), d, r["created"]))
        return out[:limit]

    # watchlist ------------------------------------------------------------
    def watch(self, key: str, reason: str, max_bid: Optional[float]) -> None:
        self._w("INSERT OR IGNORE INTO watchlist (key, added, reason, max_bid) VALUES (?,?,?,?)",
                (key, _now(), reason, max_bid))

    def unwatch(self, key: str) -> None:
        self._w("DELETE FROM watchlist WHERE key=?", (key,))

    def watchlist(self) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM watchlist ORDER BY added")

    def set_bid(self, key: str, status: str, amount: Optional[float] = None) -> None:
        self._w("UPDATE watchlist SET bid_status=?, approved_amount=COALESCE(?, approved_amount) WHERE key=?",
                (status, amount, key))

    # alerts ---------------------------------------------------------------
    def alert_sent(self, key: str, kind: str) -> bool:
        return self._q("SELECT 1 FROM alerts WHERE key=? AND kind=?", (key, kind), one=True) is not None

    def mark_alert(self, key: str, kind: str) -> None:
        self._w("INSERT OR IGNORE INTO alerts VALUES (?,?,?)", (key, kind, _now()))

    # meta (small key/value state, e.g. last poll time per source) ----------
    def get_meta(self, k: str) -> Optional[str]:
        row = self._q("SELECT v FROM meta WHERE k=?", (k,), one=True)
        return row["v"] if row else None

    def set_meta(self, k: str, v: str) -> None:
        self._w("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))
