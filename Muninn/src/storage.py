"""Conversation persistence for Huginn.

Stores conversations as JSON in a single file. Simple, human-readable, and
fine for a single-user local app. For a real multi-user or high-traffic
setup this would be SQLite or a proper DB.

All writes are atomic (write to .tmp, then replace) and guarded by a lock
so concurrent requests from the same client can't corrupt the file.
"""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


class ConversationStore:
    """Thread-safe JSON-backed conversation store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            return loaded if isinstance(loaded, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        tmp.replace(self.path)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def create(self) -> str:
        cid = uuid.uuid4().hex
        with self._lock:
            self._data[cid] = {
                "created_at": self._now(),
                "updated_at": self._now(),
                "messages": [],
            }
            self._save()
        return cid

    def exists(self, cid: str) -> bool:
        with self._lock:
            return cid in self._data

    def get(self, cid: str) -> list[dict] | None:
        """Return a deep copy of the conversation messages, or None if not found."""
        import copy
        with self._lock:
            entry = self._data.get(cid)
            if entry is None:
                return None
            return copy.deepcopy(entry["messages"])

    def append(self, cid: str, message: dict) -> bool:
        with self._lock:
            entry = self._data.get(cid)
            if entry is None:
                return False
            entry["messages"].append(message)
            entry["updated_at"] = self._now()
            self._save()
            return True

    def clear(self, cid: str) -> bool:
        with self._lock:
            entry = self._data.get(cid)
            if entry is None:
                return False
            entry["messages"] = []
            entry["updated_at"] = self._now()
            self._save()
            return True
