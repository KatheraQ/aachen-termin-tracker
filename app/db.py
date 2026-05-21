from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    verified_at TEXT,
    verify_token TEXT NOT NULL UNIQUE,
    unsubscribe_token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER NOT NULL,
    anliegen_id TEXT NOT NULL,
    PRIMARY KEY (user_id, anliegen_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS seen_slots (
    anliegen_id TEXT NOT NULL,
    slot_date TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (anliegen_id, slot_date)
);

CREATE TABLE IF NOT EXISTS notifications (
    user_id INTEGER NOT NULL,
    anliegen_id TEXT NOT NULL,
    slot_date TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (user_id, anliegen_id, slot_date)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def create_unverified_user(email: str, anliegen_ids: List[str]) -> Tuple[int, str]:
    """Create or refresh an unverified user. Returns (user_id, verify_token).

    If a verified user already exists, we still rotate the verify_token and
    require a new confirmation — that way we don't quietly resubscribe someone.
    """
    verify_token = secrets.token_urlsafe(32)
    unsubscribe_token = secrets.token_urlsafe(32)
    with connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO users (email, verify_token, unsubscribe_token, created_at) VALUES (?, ?, ?, ?)",
                (email, verify_token, unsubscribe_token, now_iso()),
            )
            user_id = cur.lastrowid
        else:
            user_id = row["id"]
            conn.execute(
                "UPDATE users SET verify_token = ?, verified_at = NULL WHERE id = ?",
                (verify_token, user_id),
            )
            conn.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        for aid in anliegen_ids:
            conn.execute(
                "INSERT OR IGNORE INTO subscriptions (user_id, anliegen_id) VALUES (?, ?)",
                (user_id, aid),
            )
    return user_id, verify_token


def verify_user(token: str) -> Optional[int]:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE verify_token = ?", (token,)
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE users SET verified_at = ? WHERE id = ?",
            (now_iso(), row["id"]),
        )
        return row["id"]


def unsubscribe(token: str) -> Optional[str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, email FROM users WHERE unsubscribe_token = ?", (token,)
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM users WHERE id = ?", (row["id"],))
        return row["email"]


def subscribers_for(anliegen_id: str) -> List[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT u.id, u.email, u.unsubscribe_token
            FROM users u
            JOIN subscriptions s ON s.user_id = u.id
            WHERE s.anliegen_id = ? AND u.verified_at IS NOT NULL
            """,
            (anliegen_id,),
        ).fetchall()


def is_slot_new(anliegen_id: str, slot_date: str) -> bool:
    """Return True if we have never recorded this slot before. Also records it."""
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_slots WHERE anliegen_id = ? AND slot_date = ?",
            (anliegen_id, slot_date),
        ).fetchone()
        if row is not None:
            return False
        conn.execute(
            "INSERT INTO seen_slots (anliegen_id, slot_date, first_seen_at) VALUES (?, ?, ?)",
            (anliegen_id, slot_date, now_iso()),
        )
        return True


def mark_seen_slots(anliegen_id: str, slot_dates: List[str]) -> List[str]:
    """Record any slots not seen before. Returns the subset that was newly inserted."""
    new = []
    for d in slot_dates:
        if is_slot_new(anliegen_id, d):
            new.append(d)
    return new


def expire_unseen_slots(anliegen_id: str, current_dates: List[str]) -> None:
    """Drop seen_slots rows for this anliegen whose date is no longer offered,
    so the same date re-appearing later counts as a fresh slot."""
    if not current_dates:
        with connect() as conn:
            conn.execute("DELETE FROM seen_slots WHERE anliegen_id = ?", (anliegen_id,))
        return
    placeholders = ",".join("?" * len(current_dates))
    with connect() as conn:
        conn.execute(
            f"DELETE FROM seen_slots WHERE anliegen_id = ? AND slot_date NOT IN ({placeholders})",
            (anliegen_id, *current_dates),
        )


def already_notified(user_id: int, anliegen_id: str, slot_date: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM notifications WHERE user_id = ? AND anliegen_id = ? AND slot_date = ?",
            (user_id, anliegen_id, slot_date),
        ).fetchone()
        return row is not None


def record_notification(user_id: int, anliegen_id: str, slot_date: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO notifications (user_id, anliegen_id, slot_date, sent_at) VALUES (?, ?, ?, ?)",
            (user_id, anliegen_id, slot_date, now_iso()),
        )
