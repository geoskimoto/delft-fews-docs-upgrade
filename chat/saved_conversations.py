"""Per-user saved conversations in SQLite.

Every method takes the owner's opaque `user_key` first and every statement is
scoped to it, so one user can never read, overwrite or delete another's rows.
The key is derived server-side from the verified token by the caller; this
module never sees, stores or logs the JWT subject (an email address).

A connection is opened per call (and the schema created on first use), which
keeps the service safe under gunicorn's 8 threads without a shared handle, and
means a missing or corrupt file surfaces as sqlite3.Error at request time
instead of preventing the whole app from starting.
"""
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from chat import config

_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_ROLES = ("user", "assistant")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    user_key   TEXT    NOT NULL,
    id         TEXT    NOT NULL,
    title      TEXT    NOT NULL,
    updated_at INTEGER NOT NULL,
    messages   TEXT    NOT NULL,
    PRIMARY KEY (user_key, id)
);
CREATE INDEX IF NOT EXISTS conversations_recent
    ON conversations (user_key, updated_at DESC);
"""


class InvalidConversation(ValueError):
    pass


def _now_ms() -> int:
    return int(time.time() * 1000)


def _byte_len(text: str) -> int:
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise InvalidConversation("message content is not valid UTF-8") from exc


def _validate_id(conv_id) -> str:
    if not isinstance(conv_id, str) or not _ID.fullmatch(conv_id):
        raise InvalidConversation("id must be 1-64 characters of A-Z a-z 0-9 _ -")
    return conv_id


def _clean_messages(messages) -> list[dict]:
    if not isinstance(messages, list) or not messages:
        raise InvalidConversation("messages must be a non-empty array")
    cleaned = []
    for item in messages:
        if not isinstance(item, dict):
            raise InvalidConversation("each message must be an object")
        role, content = item.get("role"), item.get("content")
        if role not in _ROLES:
            raise InvalidConversation("unsupported role")
        if not isinstance(content, str):
            raise InvalidConversation("message content must be a string")
        _byte_len(content)
        cleaned.append({"role": role, "content": content})
    return cleaned


def cap_messages(messages) -> list[dict]:
    """Mirror of the browser's capMessages: last N messages, newest-first byte
    budget that never drops the newest, and never begins mid-answer."""
    cleaned = _clean_messages(messages)
    start = max(0, len(cleaned) - config.MAX_SAVED_MESSAGES)
    recent = cleaned[start:]
    kept, total = [], 0
    for i in range(len(recent) - 1, -1, -1):
        size = _byte_len(recent[i]["content"])
        if kept and total + size > config.MAX_SAVED_BYTES:
            break
        total += size
        kept.append(i)
    kept.reverse()
    while len(kept) > 1 and recent[kept[0]]["role"] != "user":
        kept.pop(0)
    if len(kept) == 1 and recent[kept[0]]["role"] != "user":
        idx = kept[0]
        if idx > 0 and recent[idx - 1]["role"] == "user":
            kept.insert(0, idx - 1)
        else:
            kept.pop(0)
    out = [recent[i] for i in kept]
    if not out:
        raise InvalidConversation("nothing left to save")
    return out


def title_from(messages) -> str:
    first = next((m for m in messages if m["role"] == "user"), None)
    text = " ".join(first["content"].split()) if first else ""
    if not text:
        return "Untitled"
    limit = config.MAX_TITLE_CHARS
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _given_title(value, messages) -> str:
    if isinstance(value, str) and value.strip():
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return title_from(messages)
        return value.strip()[: config.MAX_TITLE_CHARS]
    return title_from(messages)


class ConversationDB:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _tx(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @staticmethod
    def _prune(conn, user_key: str, keep_id: str | None = None) -> None:
        # Same transaction as the write, so no reader ever sees more than the
        # cap. keep_id wins ties so the row just saved is never the one pruned.
        conn.execute(
            """DELETE FROM conversations WHERE user_key = ? AND rowid NOT IN (
                   SELECT rowid FROM conversations WHERE user_key = ?
                   ORDER BY updated_at DESC, (id = ?) DESC, rowid DESC LIMIT ?)""",
            (user_key, user_key, keep_id or "", config.MAX_SAVED_CONVERSATIONS),
        )

    def list(self, user_key: str) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute(
                """SELECT id, title, updated_at, messages FROM conversations
                   WHERE user_key = ? ORDER BY updated_at DESC, rowid DESC LIMIT ?""",
                (user_key, config.MAX_SAVED_CONVERSATIONS),
            ).fetchall()
        return [
            {"id": r[0], "title": r[1], "updatedAt": r[2],
             "messageCount": len(json.loads(r[3]))}
            for r in rows
        ]

    def get(self, user_key: str, conv_id: str) -> dict | None:
        with self._tx() as conn:
            row = conn.execute(
                """SELECT id, title, updated_at, messages FROM conversations
                   WHERE user_key = ? AND id = ?""",
                (user_key, conv_id),
            ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "title": row[1], "updatedAt": row[2],
                "messages": json.loads(row[3])}

    def save(self, user_key: str, conv_id, messages, now_ms=None) -> dict:
        conv_id = _validate_id(conv_id)
        capped = cap_messages(messages)
        now = int(now_ms) if now_ms is not None else _now_ms()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT title FROM conversations WHERE user_key = ? AND id = ?",
                (user_key, conv_id),
            ).fetchone()
            title = row[0] if row else title_from(capped)
            conn.execute(
                """INSERT INTO conversations (user_key, id, title, updated_at, messages)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (user_key, id) DO UPDATE SET
                       updated_at = excluded.updated_at, messages = excluded.messages""",
                (user_key, conv_id, title, now, json.dumps(capped)),
            )
            self._prune(conn, user_key, conv_id)
        return {"id": conv_id, "title": title, "updatedAt": now, "messages": capped}

    def delete(self, user_key: str, conv_id) -> None:
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM conversations WHERE user_key = ? AND id = ?",
                (user_key, conv_id if isinstance(conv_id, str) else ""),
            )

    def clear(self, user_key: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM conversations WHERE user_key = ?", (user_key,))

    def import_many(self, user_key: str, items, now_ms=None) -> dict:
        if not isinstance(items, list):
            raise InvalidConversation("conversations must be an array")
        now = int(now_ms) if now_ms is not None else _now_ms()

        def stamp(item):
            value = item.get("updatedAt") if isinstance(item, dict) else None
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) \
                and value == value and abs(value) != float("inf")
            return min(int(value), now) if ok else 0

        newest = sorted(items, key=stamp, reverse=True)[: config.MAX_SAVED_CONVERSATIONS]
        imported = skipped = 0
        with self._tx() as conn:
            for item in newest:
                try:
                    if not isinstance(item, dict):
                        raise InvalidConversation("not an object")
                    conv_id = _validate_id(item.get("id"))
                    capped = cap_messages(item.get("messages"))
                except InvalidConversation:
                    skipped += 1
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM conversations WHERE user_key = ? AND id = ?",
                    (user_key, conv_id),
                ).fetchone()
                if exists:
                    skipped += 1
                    continue
                conn.execute(
                    """INSERT INTO conversations
                       (user_key, id, title, updated_at, messages)
                       VALUES (?, ?, ?, ?, ?)""",
                    (user_key, conv_id, _given_title(item.get("title"), capped),
                     stamp(item), json.dumps(capped)),
                )
                imported += 1
            self._prune(conn, user_key)
        return {"imported": imported, "skipped": skipped}
