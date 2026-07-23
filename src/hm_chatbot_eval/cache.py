from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .util import stable_hash, utc_now


class ResponseCache:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        self.conn.commit()

    def key(self, namespace: str, payload: Any) -> str:
        return stable_hash({"namespace": namespace, "payload": payload})

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT response FROM cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, response: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cache(key,response,created_at) VALUES(?,?,?)",
            (key, json.dumps(response, ensure_ascii=False), utc_now()),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
