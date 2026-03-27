#!/usr/bin/env python3
"""SQLite storage for live dashboard historical data."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class ThreadSafeDB:
    """Wrapper around a sqlite3.Connection that serializes access with a lock."""

    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_time TEXT NOT NULL,
                total_games INTEGER DEFAULT 0,
                total_more INTEGER DEFAULT 0,
                has_error INTEGER DEFAULT 0,
                error_text TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS game_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
                gtype TEXT NOT NULL,
                gid TEXT DEFAULT '',
                ecid TEXT DEFAULT '',
                league TEXT DEFAULT '',
                team_h TEXT DEFAULT '',
                team_c TEXT DEFAULT '',
                score_h TEXT DEFAULT '',
                score_c TEXT DEFAULT '',
                status TEXT DEFAULT '',
                is_rb TEXT DEFAULT '',
                fields_json TEXT DEFAULT '{}',
                categories_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(snapshot_time)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_gs_gid ON game_snapshots(gid, snapshot_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_gs_ecid ON game_snapshots(ecid, snapshot_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_gs_gtype ON game_snapshots(gtype, snapshot_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_gs_created ON game_snapshots(created_at)")
        self.conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list) -> None:
        with self._lock:
            self.conn.executemany(sql, params_list)

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    @property
    def row_factory(self):
        return self.conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.conn.row_factory = value


def init_db(db_path: str) -> ThreadSafeDB:
    """Create tables and indexes. Return thread-safe wrapper."""
    return ThreadSafeDB(db_path)


def insert_snapshot(conn: ThreadSafeDB, payload: dict[str, Any]) -> int:
    """Insert a full poll payload into the database. Return snapshot_id."""
    snapshot_time = payload.get("snapshot_time", datetime.now(timezone.utc).isoformat())
    feeds = payload.get("feeds", {})
    total_games = 0
    total_more = 0
    has_error = 0
    error_parts: list[str] = []

    # Insert snapshot metadata
    cur = conn.execute(
        "INSERT INTO snapshots (snapshot_time, total_games, total_more, has_error, error_text) VALUES (?, ?, ?, ?, ?)",
        (snapshot_time, 0, 0, 0, ""),
    )
    snapshot_id = cur.lastrowid

    # Insert game snapshots for each gtype
    for gtype, feed in feeds.items():
        parsed = feed.get("parsed", {})
        games = parsed.get("games", [])
        total_games += len(games)

        if "error" in feed:
            has_error = 1
            error_parts.append(f"{gtype}: {feed['error'][:100]}")

        game_more = feed.get("game_more", {})
        total_more += len(game_more)

        for game in games:
            fields = game.get("fields", {})
            categories = game.get("categories", {})
            conn.execute(
                """INSERT INTO game_snapshots
                   (snapshot_id, gtype, gid, ecid, league, team_h, team_c,
                    score_h, score_c, status, is_rb, fields_json, categories_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id,
                    gtype,
                    game.get("gid", ""),
                    game.get("ecid", ""),
                    game.get("league", ""),
                    game.get("team_h", ""),
                    game.get("team_c", ""),
                    game.get("score_h", ""),
                    game.get("score_c", ""),
                    game.get("running", "") or game.get("retimeset", "") or game.get("now_model", ""),
                    game.get("is_rb", ""),
                    json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(categories, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    # Update snapshot totals
    conn.execute(
        "UPDATE snapshots SET total_games=?, total_more=?, has_error=?, error_text=? WHERE id=?",
        (total_games, total_more, has_error, "; ".join(error_parts), snapshot_id),
    )
    conn.commit()
    return snapshot_id


def query_game_history(
    conn: ThreadSafeDB,
    gid: str = "",
    ecid: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query historical snapshots for a specific game."""
    if not gid and not ecid:
        return []
    if ecid:
        where = "gs.ecid = ?"
        params: list[Any] = [ecid]
    else:
        where = "gs.gid = ?"
        params = [gid]

    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"""
        SELECT gs.*, s.snapshot_time
        FROM game_snapshots gs
        JOIN snapshots s ON gs.snapshot_id = s.id
        WHERE {where}
        ORDER BY gs.id DESC
        LIMIT ?
    """, (*params, limit)).fetchall()
    return [dict(r) for r in rows]


def query_snapshots(
    conn: ThreadSafeDB,
    since: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query snapshot list."""
    where_clause = ""
    params: list[Any] = []
    if since:
        where_clause = "WHERE s.created_at >= ?"
        params = [since]

    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"""
        SELECT s.id, s.snapshot_time, s.total_games, s.total_more, s.has_error, s.error_text, s.created_at
        FROM snapshots s
        {where_clause}
        ORDER BY s.id DESC
        LIMIT ?
    """, (*params, limit)).fetchall()
    return [dict(r) for r in rows]


def query_latest_games(
    conn: ThreadSafeDB,
    gtype: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Query the latest snapshot's games."""
    conn.row_factory = sqlite3.Row
    gtype_filter = ""
    params: list[Any] = []
    if gtype:
        gtype_filter = "AND gs.gtype = ?"
        params = [gtype]

    rows = conn.execute(f"""
        SELECT gs.*
        FROM game_snapshots gs
        WHERE gs.snapshot_id = (SELECT MAX(id) FROM snapshots)
        {gtype_filter}
        ORDER BY gs.gid
        LIMIT ?
    """, (*params, limit)).fetchall()
    return [dict(r) for r in rows]


def cleanup_old_data(conn: ThreadSafeDB, keep_hours: int = 24) -> int:
    """Delete snapshots older than keep_hours. Return deleted count."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=keep_hours)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "SELECT COUNT(*) FROM snapshots WHERE created_at < ?", (cutoff,)
    )
    count = cur.fetchone()[0]
    conn.execute("DELETE FROM snapshots WHERE created_at < ?", (cutoff,))
    conn.commit()
    return count


def cleanup_old_snapshot_files(outdir: Path, keep_minutes: int = 10) -> int:
    """Delete old snapshot-*.json files, keep only recent ones."""
    cutoff = time.time() - keep_minutes * 60
    deleted = 0
    for f in outdir.glob("snapshot-*.json"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1
    return deleted


def get_db_stats(conn: ThreadSafeDB, db_path: str = "") -> dict[str, Any]:
    """Return database statistics."""
    snap_count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    game_count = conn.execute("SELECT COUNT(*) FROM game_snapshots").fetchone()[0]
    earliest = conn.execute("SELECT MIN(snapshot_time) FROM snapshots").fetchone()[0]
    latest = conn.execute("SELECT MAX(snapshot_time) FROM snapshots").fetchone()[0]
    db_size = 0
    if db_path:
        try:
            db_size = Path(db_path).stat().st_size
            for suffix in ("-wal", "-shm"):
                wal = Path(db_path + suffix)
                if wal.exists():
                    db_size += wal.stat().st_size
        except OSError:
            pass
    return {
        "snapshot_count": snap_count,
        "game_snapshot_count": game_count,
        "earliest": earliest or "",
        "latest": latest or "",
        "db_size_mb": round(db_size / 1024 / 1024, 2),
    }
