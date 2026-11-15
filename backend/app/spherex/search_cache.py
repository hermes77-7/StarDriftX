"""Persist the last complete result for each SPHEREx exposure query."""

import sqlite3
from contextlib import closing
from pathlib import Path
from time import time_ns

MAX_SEARCHES = 256


class ExposureSearchCache:
    def __init__(self, path: Path):
        self.path = path

    def get(self, key: str) -> str | None:
        if not self.path.is_file():
            return None
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT result FROM searches WHERE query = ?", (key,),
            ).fetchone()
        return row[0] if row else None

    def set(self, key: str, result: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS searches (
                    query TEXT PRIMARY KEY, result TEXT NOT NULL, saved_at INTEGER NOT NULL
                )
            """)
            db.execute(
                "INSERT OR REPLACE INTO searches VALUES (?, ?, ?)", (key, result, time_ns()),
            )
            db.execute("""
                DELETE FROM searches WHERE query IN (
                    SELECT query FROM searches ORDER BY saved_at DESC LIMIT -1 OFFSET ?
                )
            """, (MAX_SEARCHES,))
