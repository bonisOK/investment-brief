"""SQLite persistence: run snapshots, ticker resolution cache, calendar events
and the log of news already shown in the brief."""
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
-- Calendar: earnings, Thai XD / pay dates and very important news. Kept
-- across runs (the Actions cache carries the DB) so past months stay filled.
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    kind TEXT NOT NULL,
    code TEXT,
    data TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS events_date ON events(date);
-- Stories the brief already showed, so the next run doesn't repeat them.
-- One row per day per book: the portfolio tabs' real total, so the rebuilt
-- history can be checked against what the sheet actually said that day.
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    date TEXT NOT NULL,
    book TEXT NOT NULL,
    total REAL,
    cash REAL,
    stock_value REAL,
    saved_at TEXT,
    PRIMARY KEY (date, book)
);
CREATE TABLE IF NOT EXISTS shown_news (
    key TEXT PRIMARY KEY,
    headline TEXT,
    shown_at TEXT NOT NULL
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


def upsert_events(events: list[dict], path: str = DB_PATH) -> None:
    """Insert or merge by id. A later run's non-null fields win; nulls never
    erase (an XD seen early with its pay date keeps it once the amount lands)."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn(path) as c:
        for e in events:
            row = c.execute("SELECT data FROM events WHERE id=?", (e["id"],)).fetchone()
            merged = json.loads(row["data"]) if row else {}
            merged.update({k: v for k, v in e.items() if v is not None})
            c.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)",
                      (merged["id"], merged["date"], merged["kind"], merged.get("code"),
                       json.dumps(merged, ensure_ascii=False), now))


def events_between(start: str, end: str, path: str = DB_PATH) -> list[dict]:
    """Events with start <= date <= end (YYYY-MM-DD), oldest first."""
    with _conn(path) as c:
        rows = c.execute("SELECT data FROM events WHERE date BETWEEN ? AND ? "
                         "ORDER BY date, id", (start, end)).fetchall()
        return [json.loads(r["data"]) for r in rows]


def prune_upcoming(kind: str, code: str, today: str, keep_ids: set[str],
                   path: str = DB_PATH) -> None:
    """Drop future `kind` events for `code` the latest fetch no longer lists
    (a rescheduled earnings date would otherwise show on both days)."""
    with _conn(path) as c:
        rows = c.execute("SELECT id FROM events WHERE kind=? AND code=? AND date>=?",
                         (kind, code, today)).fetchall()
        stale = [r["id"] for r in rows if r["id"] not in keep_ids]
        c.executemany("DELETE FROM events WHERE id=?", [(i,) for i in stale])


def mark_shown(items: list[dict], shown_at: str, path: str = DB_PATH) -> None:
    """Remember brief items ({key, headline}); first sighting keeps its time."""
    with _conn(path) as c:
        c.executemany("INSERT OR IGNORE INTO shown_news VALUES (?,?,?)",
                      [(i["key"], i.get("headline", ""), shown_at) for i in items])


def shown_keys(path: str = DB_PATH) -> set[str]:
    with _conn(path) as c:
        return {r["key"] for r in c.execute("SELECT key FROM shown_news")}


def recent_shown(since: str, path: str = DB_PATH) -> list[str]:
    """Headlines shown at or after `since` (ISO time), newest first."""
    with _conn(path) as c:
        rows = c.execute("SELECT headline FROM shown_news WHERE shown_at >= ? "
                         "ORDER BY shown_at DESC", (since,)).fetchall()
        return [r["headline"] for r in rows if r["headline"]]


def save_portfolio_snapshot(day: str, book: str, total: float, cash: float,
                            stock_value: float, path: str = DB_PATH) -> None:
    with _conn(path) as c:
        c.execute("INSERT OR REPLACE INTO portfolio_snapshots VALUES (?,?,?,?,?,?)",
                  (day, book, total, cash, stock_value, datetime.now(timezone.utc).isoformat()))


def portfolio_snapshots(book: str, path: str = DB_PATH) -> list[dict]:
    with _conn(path) as c:
        rows = c.execute("SELECT date, total, cash, stock_value FROM portfolio_snapshots "
                         "WHERE book=? ORDER BY date", (book,)).fetchall()
        return [dict(r) for r in rows]
