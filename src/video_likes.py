"""Persistent per-user likes for BirdWatch AI videos.

The bot calls init_likes() on startup, like_count()/has_liked() when rendering
video controls, and toggle_like() on callback button press.
"""

import sqlite3
from datetime import datetime, timezone

from event_log import DB_PATH, init_db


def connection():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 10000")
    return db


def init_likes():
    init_db()
    with connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS video_likes (
                event_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (event_id, user_id),
                FOREIGN KEY (event_id) REFERENCES events(id)
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_video_likes_user
            ON video_likes(user_id)
        """)


def like_count(event_id):
    with connection() as db:
        row = db.execute(
            "SELECT COUNT(*) AS n FROM video_likes WHERE event_id = ?",
            (event_id,),
        ).fetchone()
    return row["n"]


def has_liked(event_id, user_id):
    with connection() as db:
        row = db.execute(
            "SELECT 1 FROM video_likes WHERE event_id=? AND user_id=?",
            (event_id, user_id),
        ).fetchone()
    return row is not None


def toggle_like(event_id, user_id):
    """Atomically add/remove user's like. Returns (is_liked, total_likes)."""
    if not isinstance(event_id, int) or event_id <= 0:
        raise ValueError("Некорректный ID события")
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("Некорректный Telegram user_id")

    with connection() as db:
        db.execute("BEGIN IMMEDIATE")

        event = db.execute(
            "SELECT status FROM events WHERE id=?", (event_id,)
        ).fetchone()
        if event is None or event["status"] != "saved":
            raise ValueError("Это видео недоступно для голосования")

        existing = db.execute(
            "SELECT 1 FROM video_likes WHERE event_id=? AND user_id=?",
            (event_id, user_id),
        ).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO video_likes(event_id,user_id,created_at) VALUES (?,?,?)",
                (event_id, user_id, datetime.now(timezone.utc).isoformat()),
            )
            liked = True
        else:
            db.execute(
                "DELETE FROM video_likes WHERE event_id=? AND user_id=?",
                (event_id, user_id),
            )
            liked = False

        count = db.execute(
            "SELECT COUNT(*) FROM video_likes WHERE event_id=?", (event_id,)
        ).fetchone()[0]

    return liked, count


def likes_for_day(day):
    """Returns {event_id: likes} for events dated YYYY-MM-DD."""
    with connection() as db:
        rows = db.execute("""
            SELECT e.id AS event_id, COUNT(l.user_id) AS likes
            FROM events e
            LEFT JOIN video_likes l ON l.event_id = e.id
            WHERE substr(e.detected_at,1,10)=?
              AND e.status='saved' AND e.video_path IS NOT NULL
            GROUP BY e.id
        """, (day,)).fetchall()
    return {row["event_id"]: row["likes"] for row in rows}
