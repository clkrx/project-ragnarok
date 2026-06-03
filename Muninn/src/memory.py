"""SQLite-backed store for conversations, messages, and memories.

Single DB file (data/muninn_memory.db) holds three things:

- conversations + messages  -- replaces the old JSON-based store, with
  raw JSON of each message preserved so the agent's tool loop stays
  intact across turns.
- messages_fts               -- FTS5 virtual table mirroring message text
  for keyword search across past chats.
- memories + memories_fts    -- structured facts (preferences, projects,
  instructions, ...) with their own FTS5 index.

FTS5 indexes are kept in sync via triggers. On first run, any existing
data/conversations.json is migrated into the DB and the JSON file is
renamed to .migrated so the migration never re-runs.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    created_at  TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT,
    raw             TEXT,
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, COALESCE(new.content, ''));
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, COALESCE(old.content, ''));
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, COALESCE(old.content, ''));
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, COALESCE(new.content, ''));
END;

CREATE TABLE IF NOT EXISTS memories (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    content             TEXT NOT NULL,
    category            TEXT,
    importance          REAL DEFAULT 0.5,
    source_conversation TEXT,
    created_at          TEXT,
    last_accessed_at    TEXT,
    access_count        INTEGER DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    category,
    content='memories',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, category) VALUES (new.id, new.content, COALESCE(new.category, ''));
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, category) VALUES('delete', old.id, old.content, COALESCE(old.category, ''));
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, category) VALUES('delete', old.id, old.content, COALESCE(old.category, ''));
    INSERT INTO memories_fts(rowid, content, category) VALUES (new.id, new.content, COALESCE(new.category, ''));
END;
"""


class MemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._migrate_json_if_needed()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            try:
                conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
                conn.execute("DROP TABLE _fts5_probe")
            except sqlite3.OperationalError as e:
                raise RuntimeError(
                    "SQLite FTS5 is unavailable in this Python build. "
                    "Memory search requires FTS5; install a python.org "
                    "build of Python (the FTS5 module is compiled in)."
                ) from e

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _extract_text(content) -> str | None:
        if isinstance(content, str):
            return content or None
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text")
                    if text:
                        parts.append(text)
                elif btype == "tool_result":
                    result = block.get("content")
                    if isinstance(result, str) and result:
                        parts.append(result)
            return "".join(parts) if parts else None
        return None

    def _migrate_json_if_needed(self) -> None:
        json_path = self.path.parent / "conversations.json"
        if not json_path.exists():
            return
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            if count > 0:
                return
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return
            for cid, entry in data.items():
                conn.execute(
                    "INSERT OR IGNORE INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
                    (
                        cid,
                        entry.get("created_at"),
                        entry.get("updated_at"),
                    ),
                )
                for msg in entry.get("messages", []):
                    self._insert_message(conn, cid, msg)
            json_path.rename(json_path.with_suffix(".json.migrated"))

    def _insert_message(self, conn, conversation_id: str, message: dict) -> None:
        role = message.get("role")
        content = message.get("content")
        text = self._extract_text(content)
        raw = json.dumps(message, ensure_ascii=False)
        conn.execute(
            """INSERT INTO messages (conversation_id, role, content, raw, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (conversation_id, role, text, raw, self._now()),
        )

    def create(self, cid: str | None = None) -> str:
        import uuid

        if cid is None:
            cid = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
                (cid, self._now(), self._now()),
            )
        return cid

    def get_or_create(self, cid: str) -> str:
        return self.create(cid)

    def exists(self, cid: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (cid,)
            ).fetchone()
            return row is not None

    def get(self, cid: str) -> list[dict] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (cid,)
            ).fetchone()
            if row is None:
                return None
            rows = conn.execute(
                "SELECT raw, content FROM messages WHERE conversation_id = ? ORDER BY id",
                (cid,),
            ).fetchall()
            result: list[dict] = []
            for r in rows:
                if r["raw"]:
                    try:
                        result.append(json.loads(r["raw"]))
                        continue
                    except json.JSONDecodeError:
                        pass
                result.append({"role": "user", "content": r["content"] or ""})
            return result

    def append(self, cid: str, message: dict) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (cid,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
                    (cid, self._now(), self._now()),
                )
            self._insert_message(conn, cid, message)
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (self._now(), cid),
            )
        return True

    def clear(self, cid: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (cid,)
            ).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (self._now(), cid),
            )
        return True

    def list_conversations(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, updated_at FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def add_memory(
        self,
        content: str,
        category: str | None = None,
        importance: float = 0.5,
        source_conversation: str | None = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO memories
                   (content, category, importance, source_conversation, created_at, last_accessed_at, access_count)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (
                    content,
                    category,
                    float(importance),
                    source_conversation,
                    self._now(),
                    self._now(),
                ),
            )
            return int(cur.lastrowid)

    def get_memory(self, memory_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_memory(self, memory_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return cur.rowcount > 0

    def list_memories(
        self, category: str | None = None, limit: int = 20
    ) -> list[dict]:
        with self._connect() as conn:
            if category:
                rows = conn.execute(
                    """SELECT * FROM memories WHERE category = ?
                       ORDER BY importance DESC, last_accessed_at DESC LIMIT ?""",
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM memories
                       ORDER BY importance DESC, last_accessed_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def search(
        self,
        query: str,
        *,
        conversations_limit: int = 5,
        memories_limit: int = 5,
    ) -> dict[str, list[dict]]:
        return {
            "conversations": self._search_conversations(query, conversations_limit),
            "memories": self._search_memories(query, memories_limit),
        }

    @staticmethod
    def _fts_escape(query: str) -> str:
        return '"' + query.replace('"', '""') + '"'

    def _search_conversations(self, query: str, limit: int) -> list[dict]:
        if not query.strip():
            return []
        fts_query = self._fts_escape(query)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT
                       m.conversation_id,
                       m.role,
                       m.content,
                       m.created_at,
                       snippet(messages_fts, 0, '[[', ']]', '...', 16) AS snippet
                   FROM messages_fts
                   JOIN messages m ON m.id = messages_fts.rowid
                   WHERE messages_fts MATCH ?
                     AND m.content IS NOT NULL
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def _search_memories(self, query: str, limit: int) -> list[dict]:
        if not query.strip():
            return []
        fts_query = self._fts_escape(query)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT
                       m.id,
                       m.content,
                       m.category,
                       m.importance,
                       m.source_conversation,
                       m.created_at,
                       snippet(memories_fts, 0, '[[', ']]', '...', 16) AS snippet
                   FROM memories_fts
                   JOIN memories m ON m.id = memories_fts.rowid
                   WHERE memories_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
            results = [dict(r) for r in rows]
            for r in results:
                conn.execute(
                    "UPDATE memories SET last_accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
                    (self._now(), r["id"]),
                )
            return results
