"""
nano/db.py
SQLite storage layer for NanoSideband.

Tables
------
  contacts   — known peers (hash, display_name, trusted, notes)
  messages   — inbound + outbound LXMF messages
  images     — image blobs keyed by message hash

Design mirrors Sideband's database patterns (verified from core.py source)
but is stripped to only what NanoSideband needs.

All public methods are thread-safe via a single Lock.
"""

import sqlite3
import threading
import time
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    dest_hash   TEXT PRIMARY KEY,   -- hex, 32 chars (16 bytes)
    display_name TEXT NOT NULL DEFAULT '',
    trusted     INTEGER NOT NULL DEFAULT 0,  -- 0=untrusted, 1=trusted
    notes       TEXT NOT NULL DEFAULT '',
    first_seen  REAL NOT NULL,      -- unix timestamp
    last_seen   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    msg_hash    TEXT PRIMARY KEY,   -- hex of LXMessage.hash (64 chars)
    dest_hash   TEXT NOT NULL,      -- recipient hex
    source_hash TEXT NOT NULL,      -- sender hex
    content     TEXT NOT NULL DEFAULT '',
    timestamp   REAL NOT NULL,      -- LXMessage.timestamp (sender clock)
    rx_ts       REAL,               -- when WE received it (NULL if outbound)
    tx_ts       REAL,               -- when WE sent it (NULL if inbound)
    direction   TEXT NOT NULL,      -- 'in' or 'out'
    state       TEXT NOT NULL DEFAULT 'pending',
                                    -- pending/sent/delivered/failed
    has_image   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_dest   ON messages(dest_hash);
CREATE INDEX IF NOT EXISTS idx_messages_source ON messages(source_hash);
CREATE INDEX IF NOT EXISTS idx_messages_ts     ON messages(timestamp);

CREATE TABLE IF NOT EXISTS images (
    msg_hash    TEXT PRIMARY KEY,
    image_data  BLOB NOT NULL,
    mime_type   TEXT NOT NULL DEFAULT 'image/jpeg',
    width       INTEGER,
    height      INTEGER,
    stored_ts   REAL NOT NULL
);
"""

# ── NanoDB ────────────────────────────────────────────────────────────────────

class NanoDB:
    """
    Thread-safe SQLite wrapper.

    Usage:
        db = NanoDB(config.db_path)
        db.open()
        db.save_message(...)
        db.close()

    Or as a context manager:
        with NanoDB(config.db_path) as db:
            db.save_message(...)
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def open(self) -> None:
        """Open (or create) the database and apply schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread = False,
            timeout           = 10.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        log.info("Database opened: %s", self.db_path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            log.info("Database closed.")

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()   # auto-open on first use (Android-friendly)
        return self._conn

    # ── Contacts ──────────────────────────────────────────────────────────────

    def upsert_contact(
        self,
        dest_hash: str,
        display_name: str = "",
        trusted: bool = False,
        notes: str = "",
    ) -> None:
        """
        Insert or update a contact.
        dest_hash is the hex LXMF destination hash (32 chars).
        """
        now = time.time()
        with self._lock:
            db = self._db()
            existing = db.execute(
                "SELECT dest_hash FROM contacts WHERE dest_hash=?",
                (dest_hash,)
            ).fetchone()

            if existing:
                db.execute(
                    """UPDATE contacts
                       SET display_name=?, trusted=?, notes=?, last_seen=?
                       WHERE dest_hash=?""",
                    (display_name, int(trusted), notes, now, dest_hash),
                )
            else:
                db.execute(
                    """INSERT INTO contacts
                       (dest_hash, display_name, trusted, notes, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?)""",
                    (dest_hash, display_name, int(trusted), notes, now, now),
                )
            db.commit()

    def get_contact(self, dest_hash: str) -> Optional[dict]:
        """Return contact dict or None."""
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM contacts WHERE dest_hash=?", (dest_hash,)
            ).fetchone()
        return dict(row) if row else None

    def list_contacts(self, trusted_only: bool = False) -> list[dict]:
        """Return all contacts, optionally filtered to trusted only."""
        with self._lock:
            if trusted_only:
                rows = self._db().execute(
                    "SELECT * FROM contacts WHERE trusted=1 ORDER BY last_seen DESC"
                ).fetchall()
            else:
                rows = self._db().execute(
                    "SELECT * FROM contacts ORDER BY last_seen DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def set_trusted(self, dest_hash: str, trusted: bool) -> None:
        with self._lock:
            self._db().execute(
                "UPDATE contacts SET trusted=? WHERE dest_hash=?",
                (int(trusted), dest_hash)
            )
            self._db().commit()

    def delete_contact(self, dest_hash: str) -> None:
        with self._lock:
            self._db().execute(
                "DELETE FROM contacts WHERE dest_hash=?", (dest_hash,)
            )
            self._db().commit()

    def touch_contact(self, dest_hash: str, display_name: str = "") -> None:
        """Update last_seen (and display_name if provided). Creates if missing."""
        existing = self.get_contact(dest_hash)
        if existing:
            with self._lock:
                params = [time.time()]
                if display_name:
                    self._db().execute(
                        "UPDATE contacts SET last_seen=?, display_name=? WHERE dest_hash=?",
                        (time.time(), display_name, dest_hash)
                    )
                else:
                    self._db().execute(
                        "UPDATE contacts SET last_seen=? WHERE dest_hash=?",
                        (time.time(), dest_hash)
                    )
                self._db().commit()
        else:
            self.upsert_contact(dest_hash, display_name=display_name)

    # ── Messages ──────────────────────────────────────────────────────────────

    def save_message(
        self,
        msg_hash: str,
        dest_hash: str,
        source_hash: str,
        content: str,
        timestamp: float,
        direction: str,         # 'in' or 'out'
        state: str = "pending",
        has_image: bool = False,
        rx_ts: float = None,
        tx_ts: float = None,
    ) -> bool:
        """
        Save a message. Returns False if msg_hash already exists (duplicate).
        """
        with self._lock:
            existing = self._db().execute(
                "SELECT msg_hash FROM messages WHERE msg_hash=?", (msg_hash,)
            ).fetchone()
            if existing:
                log.debug("Duplicate message %s, skipping.", msg_hash)
                return False

            self._db().execute(
                """INSERT INTO messages
                   (msg_hash, dest_hash, source_hash, content, timestamp,
                    rx_ts, tx_ts, direction, state, has_image)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    msg_hash, dest_hash, source_hash, content, timestamp,
                    rx_ts, tx_ts, direction, state, int(has_image),
                )
            )
            self._db().commit()
        return True

    def update_message_state(self, msg_hash: str, state: str) -> None:
        """Update delivery state: pending/sent/delivered/failed."""
        with self._lock:
            self._db().execute(
                "UPDATE messages SET state=? WHERE msg_hash=?",
                (state, msg_hash)
            )
            self._db().commit()

    def get_message(self, msg_hash: str) -> Optional[dict]:
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM messages WHERE msg_hash=?", (msg_hash,)
            ).fetchone()
        return dict(row) if row else None

    def list_messages(
        self,
        peer_hash: str,
        limit: int = 50,
        before_ts: float = None,
    ) -> list[dict]:
        """
        Return messages in a conversation with peer_hash,
        most recent first. before_ts enables pagination.
        """
        with self._lock:
            if before_ts:
                rows = self._db().execute(
                    """SELECT * FROM messages
                       WHERE (dest_hash=? OR source_hash=?)
                         AND timestamp < ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (peer_hash, peer_hash, before_ts, limit)
                ).fetchall()
            else:
                rows = self._db().execute(
                    """SELECT * FROM messages
                       WHERE dest_hash=? OR source_hash=?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (peer_hash, peer_hash, limit)
                ).fetchall()
        return [dict(r) for r in rows]

    def list_conversations(self) -> list[dict]:
        """
        Return one row per peer showing the latest message and unread count.
        Mirrors Sideband's list_conversations() concept.
        """
        with self._lock:
            rows = self._db().execute(
                """
                SELECT
                    peer,
                    MAX(timestamp)  AS last_ts,
                    COUNT(*)        AS total_msgs,
                    SUM(CASE WHEN direction='in' AND state='pending' THEN 1 ELSE 0 END)
                                    AS unread
                FROM (
                    SELECT dest_hash   AS peer, timestamp, direction, state FROM messages
                    UNION ALL
                    SELECT source_hash AS peer, timestamp, direction, state FROM messages
                )
                GROUP BY peer
                ORDER BY last_ts DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_message(self, msg_hash: str) -> None:
        with self._lock:
            self._db().execute(
                "DELETE FROM messages WHERE msg_hash=?", (msg_hash,)
            )
            self._db().execute(
                "DELETE FROM images WHERE msg_hash=?", (msg_hash,)
            )
            self._db().commit()

    def clear_conversation(self, peer_hash: str) -> int:
        """Delete all messages with peer. Returns count deleted."""
        with self._lock:
            # Get hashes so we can also remove images
            hashes = [
                r[0] for r in self._db().execute(
                    """SELECT msg_hash FROM messages
                       WHERE dest_hash=? OR source_hash=?""",
                    (peer_hash, peer_hash)
                ).fetchall()
            ]
            self._db().execute(
                "DELETE FROM messages WHERE dest_hash=? OR source_hash=?",
                (peer_hash, peer_hash)
            )
            if hashes:
                placeholders = ",".join("?" * len(hashes))
                self._db().execute(
                    f"DELETE FROM images WHERE msg_hash IN ({placeholders})",
                    hashes
                )
            self._db().commit()
        return len(hashes)

    def mark_read(self, peer_hash: str) -> None:
        """Mark all inbound messages from peer as read (state=delivered)."""
        with self._lock:
            self._db().execute(
                """UPDATE messages SET state='delivered'
                   WHERE source_hash=? AND direction='in' AND state='pending'""",
                (peer_hash,)
            )
            self._db().commit()

    def message_count(self, peer_hash: str) -> int:
        with self._lock:
            row = self._db().execute(
                "SELECT COUNT(*) FROM messages WHERE dest_hash=? OR source_hash=?",
                (peer_hash, peer_hash)
            ).fetchone()
        return row[0] if row else 0

    # ── Images ────────────────────────────────────────────────────────────────

    def save_image(
        self,
        msg_hash: str,
        image_data: bytes,
        mime_type: str = "image/jpeg",
        width: int = None,
        height: int = None,
    ) -> None:
        with self._lock:
            self._db().execute(
                """INSERT OR REPLACE INTO images
                   (msg_hash, image_data, mime_type, width, height, stored_ts)
                   VALUES (?,?,?,?,?,?)""",
                (msg_hash, image_data, mime_type, width, height, time.time())
            )
            self._db().commit()

    def get_image(self, msg_hash: str) -> Optional[dict]:
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM images WHERE msg_hash=?", (msg_hash,)
            ).fetchone()
        return dict(row) if row else None

    def image_exists(self, msg_hash: str) -> bool:
        with self._lock:
            row = self._db().execute(
                "SELECT 1 FROM images WHERE msg_hash=?", (msg_hash,)
            ).fetchone()
        return row is not None

    # ── Stats / housekeeping ──────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return basic database statistics."""
        with self._lock:
            contacts = self._db().execute(
                "SELECT COUNT(*) FROM contacts"
            ).fetchone()[0]
            messages = self._db().execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()[0]
            images = self._db().execute(
                "SELECT COUNT(*) FROM images"
            ).fetchone()[0]
            unread = self._db().execute(
                "SELECT COUNT(*) FROM messages WHERE direction='in' AND state='pending'"
            ).fetchone()[0]
        return {
            "contacts": contacts,
            "messages": messages,
            "images":   images,
            "unread":   unread,
            "db_path":  str(self.db_path),
        }

    def purge_old_messages(self, older_than_days: int = 90) -> int:
        """Delete messages older than N days. Returns count deleted."""
        cutoff = time.time() - older_than_days * 86400
        with self._lock:
            hashes = [
                r[0] for r in self._db().execute(
                    "SELECT msg_hash FROM messages WHERE timestamp < ?", (cutoff,)
                ).fetchall()
            ]
            if hashes:
                placeholders = ",".join("?" * len(hashes))
                self._db().execute(
                    f"DELETE FROM messages WHERE msg_hash IN ({placeholders})", hashes
                )
                self._db().execute(
                    f"DELETE FROM images WHERE msg_hash IN ({placeholders})", hashes
                )
                self._db().commit()
        return len(hashes)
