"""SQLite storage for download history and user preferences.

Replaces per-user JSON files with a single concurrent-safe database.
"""

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("mangko")

_DB: sqlite3.Connection | None = None


def init_db(db_path: Path = Path("mangko.db")) -> sqlite3.Connection:
    global _DB
    if _DB is not None:
        return _DB
    _DB = sqlite3.connect(str(db_path), check_same_thread=False)
    _DB.execute("PRAGMA journal_mode=WAL")
    _DB.execute("PRAGMA busy_timeout=5000")
    _DB.executescript("""
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            filename TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            quality TEXT NOT NULL DEFAULT 'best',
            timestamp REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dl_user ON downloads(user_id);

        CREATE TABLE IF NOT EXISTS user_prefs (
            user_id INTEGER PRIMARY KEY,
            quality TEXT NOT NULL DEFAULT 'best',
            subtitle TEXT NOT NULL DEFAULT 'en'
        );
    """)
    _DB.commit()
    return _DB


# ── History ──────────────────────────────────────────────

def save_download(
    user_id: int,
    url: str,
    filename: str,
    size: int,
    quality: str,
) -> None:
    db = init_db()
    db.execute(
        "INSERT INTO downloads (user_id, url, filename, size, quality, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, url, filename, size, quality, time.time()),
    )
    db.commit()


def get_history(user_id: int, limit: int = 10) -> list[dict]:
    db = init_db()
    rows = db.execute(
        "SELECT filename, size, quality, timestamp FROM downloads "
        "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [
        {"filename": r[0], "size": r[1], "quality": r[2], "timestamp": r[3]}
        for r in rows
    ]


def get_stats(user_id: int) -> dict:
    db = init_db()
    row = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM downloads WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    fmt_rows = db.execute(
        "SELECT quality, COUNT(*) FROM downloads WHERE user_id = ? GROUP BY quality",
        (user_id,),
    ).fetchall()
    return {
        "total_downloads": row[0],
        "total_size": row[1],
        "formats": {r[0]: r[1] for r in fmt_rows},
    }


# ── User Preferences ─────────────────────────────────────

def get_user_quality(user_id: int, default: str = "best") -> str:
    db = init_db()
    row = db.execute(
        "SELECT quality FROM user_prefs WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else default


def get_user_subtitle(user_id: int, default: str = "en") -> str:
    db = init_db()
    row = db.execute(
        "SELECT subtitle FROM user_prefs WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else default


def set_user_quality(user_id: int, quality: str) -> None:
    db = init_db()
    db.execute(
        "INSERT INTO user_prefs (user_id, quality, subtitle) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET quality = excluded.quality",
        (user_id, quality, "en"),
    )
    db.commit()


def set_user_subtitle(user_id: int, subtitle: str) -> None:
    db = init_db()
    db.execute(
        "INSERT INTO user_prefs (user_id, quality, subtitle) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET subtitle = excluded.subtitle",
        (user_id, "best", subtitle),
    )
    db.commit()


def get_user_prefs(user_id: int) -> dict[str, str]:
    db = init_db()
    row = db.execute(
        "SELECT quality, subtitle FROM user_prefs WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row:
        return {"quality": row[0], "subtitle": row[1]}
    return {"quality": "best", "subtitle": "en"}
