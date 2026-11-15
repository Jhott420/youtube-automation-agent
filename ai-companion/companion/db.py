"""SQLite storage for users, conversation turns and long-term memories.

WAL mode lets the bot, the API and the admin CLI (separate processes) share
one database file safely -- that's how `admin pause` takes effect instantly.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    display_name  TEXT,
    platform      TEXT NOT NULL DEFAULT 'unknown',
    created_at    TEXT NOT NULL,
    disclosed_at  TEXT,                -- when the AI disclosure was first sent
    paused        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    summarized  INTEGER NOT NULL DEFAULT 0,
    flag        TEXT                   -- optional marker; flagged turns are never summarized
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'summary',   -- 'summary' | 'manual'
    embedder    TEXT NOT NULL,                     -- which embedder produced `embedding`
    embedding   TEXT NOT NULL,                     -- JSON list of floats
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class User:
    user_id: str
    display_name: str | None
    platform: str
    created_at: str
    disclosed_at: str | None
    paused: bool


@dataclass
class Message:
    id: int
    user_id: str
    role: str
    content: str
    created_at: str
    summarized: bool
    flag: str | None


@dataclass
class MemoryRow:
    id: int
    user_id: str
    content: str
    source: str
    embedder: str
    embedding: list[float]
    created_at: str
    updated_at: str


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ---- users ---------------------------------------------------------------

    def get_or_create_user(
        self, user_id: str, display_name: str | None = None, platform: str = "unknown"
    ) -> User:
        user = self.get_user(user_id)
        if user:
            if display_name and display_name != user.display_name:
                self._exec("UPDATE users SET display_name=? WHERE user_id=?", (display_name, user_id))
                user.display_name = display_name
            return user
        self._exec(
            "INSERT INTO users (user_id, display_name, platform, created_at) VALUES (?,?,?,?)",
            (user_id, display_name, platform, now_iso()),
        )
        return self.get_user(user_id)  # type: ignore[return-value]

    def get_user(self, user_id: str) -> User | None:
        rows = self._query("SELECT * FROM users WHERE user_id=?", (user_id,))
        return _user(rows[0]) if rows else None

    def list_users(self) -> list[User]:
        return [_user(r) for r in self._query("SELECT * FROM users ORDER BY created_at")]

    def mark_disclosed(self, user_id: str) -> None:
        self._exec(
            "UPDATE users SET disclosed_at=? WHERE user_id=? AND disclosed_at IS NULL",
            (now_iso(), user_id),
        )

    def set_paused(self, user_id: str, paused: bool) -> bool:
        """Returns False if the user doesn't exist yet."""
        cur = self._exec("UPDATE users SET paused=? WHERE user_id=?", (int(paused), user_id))
        return cur.rowcount > 0

    # ---- messages ------------------------------------------------------------

    def add_message(self, user_id: str, role: str, content: str, flag: str | None = None) -> int:
        cur = self._exec(
            "INSERT INTO messages (user_id, role, content, created_at, flag) VALUES (?,?,?,?,?)",
            (user_id, role, content, now_iso(), flag),
        )
        return int(cur.lastrowid)

    def recent_messages(self, user_id: str, limit: int) -> list[Message]:
        rows = self._query(
            "SELECT * FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        )
        return [_message(r) for r in reversed(rows)]

    def all_messages(self, user_id: str, limit: int | None = None) -> list[Message]:
        if limit is None:
            rows = self._query("SELECT * FROM messages WHERE user_id=? ORDER BY id", (user_id,))
            return [_message(r) for r in rows]
        return self.recent_messages(user_id, limit)

    def unsummarized_outside_window(self, user_id: str, window: int) -> list[Message]:
        """Messages not yet summarized that are older than the last `window` messages."""
        rows = self._query(
            """
            SELECT * FROM messages
            WHERE user_id=? AND summarized=0
              AND id < COALESCE((
                  SELECT MIN(id) FROM (
                      SELECT id FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?
                  )
              ), 9223372036854775807)
            ORDER BY id
            """,
            (user_id, user_id, window),
        )
        return [_message(r) for r in rows]

    def mark_summarized(self, message_ids: list[int]) -> None:
        if not message_ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE messages SET summarized=1 WHERE id=?", [(i,) for i in message_ids]
            )
            self._conn.commit()

    # ---- memories ------------------------------------------------------------

    def add_memory(
        self, user_id: str, content: str, embedder: str, embedding: list[float], source: str
    ) -> int:
        ts = now_iso()
        cur = self._exec(
            """INSERT INTO memories (user_id, content, source, embedder, embedding, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, content, source, embedder, json.dumps(embedding), ts, ts),
        )
        return int(cur.lastrowid)

    def get_memory(self, memory_id: int) -> MemoryRow | None:
        rows = self._query("SELECT * FROM memories WHERE id=?", (memory_id,))
        return _memory(rows[0]) if rows else None

    def list_memories(self, user_id: str) -> list[MemoryRow]:
        return [_memory(r) for r in self._query("SELECT * FROM memories WHERE user_id=? ORDER BY id", (user_id,))]

    def update_memory(self, memory_id: int, content: str, embedder: str, embedding: list[float]) -> bool:
        cur = self._exec(
            "UPDATE memories SET content=?, embedder=?, embedding=?, updated_at=? WHERE id=?",
            (content, embedder, json.dumps(embedding), now_iso(), memory_id),
        )
        return cur.rowcount > 0

    def update_memory_embedding(self, memory_id: int, embedder: str, embedding: list[float]) -> None:
        self._exec(
            "UPDATE memories SET embedder=?, embedding=? WHERE id=?",
            (embedder, json.dumps(embedding), memory_id),
        )

    def delete_memory(self, memory_id: int) -> bool:
        return self._exec("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount > 0

    def wipe_memories(self, user_id: str) -> int:
        """Delete all long-term memories for a user. Already-summarized messages stay
        marked as summarized, so wiped facts are not re-extracted later."""
        return self._exec("DELETE FROM memories WHERE user_id=?", (user_id,)).rowcount

    def wipe_conversation(self, user_id: str) -> int:
        return self._exec("DELETE FROM messages WHERE user_id=?", (user_id,)).rowcount


def _user(r: sqlite3.Row) -> User:
    return User(r["user_id"], r["display_name"], r["platform"], r["created_at"], r["disclosed_at"], bool(r["paused"]))


def _message(r: sqlite3.Row) -> Message:
    return Message(r["id"], r["user_id"], r["role"], r["content"], r["created_at"], bool(r["summarized"]), r["flag"])


def _memory(r: sqlite3.Row) -> MemoryRow:
    return MemoryRow(
        r["id"], r["user_id"], r["content"], r["source"], r["embedder"],
        json.loads(r["embedding"]), r["created_at"], r["updated_at"],
    )
