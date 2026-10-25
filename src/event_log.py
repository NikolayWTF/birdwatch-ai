
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


DB_PATH = Path("data/events.db")


def utc_now():
    return datetime.now(timezone.utc)


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")

    return connection


def init_db():
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL,
                video_path TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS telegram_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                sent_at TEXT,
                telegram_message_id INTEGER,
                last_error TEXT,
                FOREIGN KEY (event_id) REFERENCES events(id)
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_outbox_pending
            ON telegram_outbox(status, next_attempt_at)
        """)


def create_event(detected_at, confidence):
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO events (
                detected_at, confidence, status
            )
            VALUES (?, ?, 'recording')
            """,
            (
                detected_at.isoformat(timespec="seconds"),
                confidence,
            ),
        )

        return cursor.lastrowid


def update_event(event_id, status, video_path=None):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE events
            SET status = ?, video_path = ?
            WHERE id = ?
            """,
            (
                status,
                str(video_path) if video_path else None,
                event_id,
            ),
        )


def queue_video(event_id):
    now = utc_now().isoformat()

    with get_connection() as connection:
        event = connection.execute(
            """
            SELECT status, video_path
            FROM events
            WHERE id = ?
            """,
            (event_id,),
        ).fetchone()

        if event is None:
            raise ValueError(f"Событие #{event_id} не найдено")

        if event["status"] != "saved" or not event["video_path"]:
            raise ValueError(
                f"Событие #{event_id} не содержит готовое видео"
            )

        connection.execute(
            """
            INSERT OR IGNORE INTO telegram_outbox (
                event_id, status, next_attempt_at
            )
            VALUES (?, 'pending', ?)
            """,
            (event_id, now),
        )


def reset_interrupted_deliveries():
    with get_connection() as connection:
        connection.execute("""
            UPDATE telegram_outbox
            SET status = 'pending',
                next_attempt_at = ?
            WHERE status = 'sending'
        """, (utc_now().isoformat(),))


def claim_next_delivery():
    now = utc_now().isoformat()

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")

        row = connection.execute(
            """
            SELECT
                o.id AS outbox_id,
                o.event_id,
                o.attempts,
                e.detected_at,
                e.confidence,
                e.video_path
            FROM telegram_outbox o
            JOIN events e ON e.id = o.event_id
            WHERE o.status = 'pending'
              AND o.next_attempt_at <= ?
            ORDER BY o.next_attempt_at, o.id
            LIMIT 1
            """,
            (now,),
        ).fetchone()

        if row is None:
            return None

        connection.execute(
            """
            UPDATE telegram_outbox
            SET status = 'sending',
                attempts = attempts + 1
            WHERE id = ?
            """,
            (row["outbox_id"],),
        )

        return dict(row)


def mark_delivery_sent(outbox_id, message_id):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE telegram_outbox
            SET status = 'sent',
                sent_at = ?,
                telegram_message_id = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (utc_now().isoformat(), message_id, outbox_id),
        )


def mark_delivery_failed(outbox_id, attempts, error):
    # 15, 30, 60, 120 ... секунд, максимум 15 минут.
    delay = min(15 * (2 ** min(attempts, 6)), 900)

    next_attempt = (
        utc_now() + timedelta(seconds=delay)
    ).isoformat()

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE telegram_outbox
            SET status = 'pending',
                next_attempt_at = ?,
                last_error = ?
            WHERE id = ?
            """,
            (next_attempt, str(error)[:500], outbox_id),
        )

    return delay


def get_delivery_stats():
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT status, COUNT(*) AS total
            FROM telegram_outbox
            GROUP BY status
        """).fetchall()

    stats = {"pending": 0, "sending": 0, "sent": 0}

    for row in rows:
        stats[row["status"]] = row["total"]

    return stats


if __name__ == "__main__":
    init_db()
    print("База данных готова!")
    print("Telegram:", get_delivery_stats())
