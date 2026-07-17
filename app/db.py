"""SQLite persistence: run snapshots + ticker resolution cache."""
import json
import os
import sqlite3
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    fng INTEGER,
    fng_label TEXT,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticker_map (
    dr_code TEXT PRIMARY KEY,
    real_ticker TEXT NOT NULL,
    company_name TEXT,
    exchange TEXT,
    resolved_at TEXT
);
"""


def _conn(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save_run(payload: dict, path: str = DB_PATH) -> int:
    with _conn(path) as c:
        cur = c.execute(
            "INSERT INTO runs (run_at, fng, fng_label, data) VALUES (?,?,?,?)",
            (
                payload.get("run_at", datetime.now(timezone.utc).isoformat()),
                (payload.get("fng") or {}).get("score"),
                (payload.get("fng") or {}).get("label"),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        return cur.lastrowid


def latest_run(path: str = DB_PATH) -> dict | None:
    with _conn(path) as c:
        row = c.execute("SELECT data FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row["data"]) if row else None


def run_history(limit: int = 30, path: str = DB_PATH) -> list[dict]:
    with _conn(path) as c:
        rows = c.execute(
            "SELECT id, run_at, fng, fng_label FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_cached_ticker(dr_code: str, path: str = DB_PATH) -> dict | None:
    with _conn(path) as c:
        row = c.execute(
            "SELECT real_ticker, company_name, exchange FROM ticker_map WHERE dr_code=?",
            (dr_code.upper(),),
        ).fetchone()
        return dict(row) if row else None


def cache_ticker(dr_code: str, real_ticker: str, company_name: str, exchange: str,
                 path: str = DB_PATH) -> None:
    with _conn(path) as c:
        c.execute(
            "INSERT OR REPLACE INTO ticker_map VALUES (?,?,?,?,?)",
            (dr_code.upper(), real_ticker, company_name, exchange,
             datetime.now(timezone.utc).isoformat()),
        )
