"""Durable checkpoints; model predictions and explicit confirmations are separate."""

import datetime as dt
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS records(kind TEXT, id TEXT, data TEXT,
                PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, data TEXT, at TEXT);
            CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, batch TEXT, kind TEXT,
                at TEXT, status TEXT, usage TEXT);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def put(self, kind, obj):
        obj.setdefault("id", uuid.uuid4().hex)
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO records VALUES (?,?,?)",
                (kind, obj["id"], json.dumps(obj, ensure_ascii=False)),
            )
        return obj

    def get(self, kind, ident):
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM records WHERE kind=? AND id=?", (kind, ident)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, kind):
        with self.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute("SELECT data FROM records WHERE kind=? ORDER BY rowid", (kind,))
            ]

    def items(self, batch):
        return [r for r in self.all("item") if r["batch"] == batch]

    def cached(self, key, ttl_days=7):
        with self.connect() as db:
            row = db.execute("SELECT data,at FROM cache WHERE key=?", (key,)).fetchone()
        if (
            row
            and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(row[1])).days
            < ttl_days
        ):
            return json.loads(row[0])

    def cache(self, key, value):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, json.dumps(value), now())
            )

    def clear_cache(self):
        with self.connect() as db:
            db.execute("DELETE FROM cache")

    def call(self, batch, kind, status, usage=None):
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO calls(batch,kind,at,status,usage) VALUES (?,?,?,?,?)",
                (batch, kind, now(), status, json.dumps(usage or {})),
            )
            return cursor.lastrowid

    def finish_call(self, ident, status, usage=None):
        with self.connect() as db:
            db.execute(
                "UPDATE calls SET status=?,usage=? WHERE id=?",
                (status, json.dumps(usage or {}), ident),
            )

    def usage(self, batch):
        with self.connect() as db:
            rows = db.execute(
                "SELECT kind,status,usage FROM calls WHERE batch=?", (batch,)
            ).fetchall()
        return {
            "requests": len(rows),
            "search_requests": sum(r[0] == "search" for r in rows),
            "responses": [json.loads(r[2]) for r in rows if r[1] == "ok"],
        }
