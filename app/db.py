"""Tiny SQLite index: dedupe per person + where each post ended up."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS items (
                profile TEXT NOT NULL, platform TEXT NOT NULL, post_id TEXT NOT NULL,
                notion_url TEXT, s3_prefix TEXT, created_at INTEGER NOT NULL,
                PRIMARY KEY (profile, platform, post_id))"""
        )
        self.conn.commit()

    def get(self, profile: str, platform: str, post_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT notion_url FROM items WHERE profile=? AND platform=? AND post_id=?",
            (profile, platform, post_id),
        ).fetchone()
        return row[0] if row else None

    def put(self, profile: str, platform: str, post_id: str, notion_url: str, s3_prefix: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?)",
            (profile, platform, post_id, notion_url, s3_prefix, int(time.time())),
        )
        self.conn.commit()
