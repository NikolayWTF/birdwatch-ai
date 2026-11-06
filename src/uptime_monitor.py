
import sqlite3
import threading
import time
from datetime import datetime

from event_log import DB_PATH


CHECK_INTERVAL = 5


def init_uptime_db():
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS camera_uptime (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                duration_seconds REAL NOT NULL
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_camera_uptime_date
            ON camera_uptime(observed_at)
        """)


def save_uptime(seconds):
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute(
            """
            INSERT INTO camera_uptime (
                observed_at, duration_seconds
            )
            VALUES (?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                seconds,
            ),
        )


def get_uptime_stats(day=None):
    if day is None:
        day = datetime.now().date().isoformat()

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(duration_seconds), 0)
            FROM camera_uptime
            WHERE substr(observed_at, 1, 10) = ?
            """,
            (day,),
        ).fetchone()

    return {
        "samples": row[0],
        "seconds": row[1],
        "hours": row[1] / 3600,
    }


class UptimeMonitor:
    def __init__(self, camera):
        self.camera = camera
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        init_uptime_db()

        self.stop_event.clear()

        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
        )

        self.thread.start()
        print("[Uptime] Мониторинг камеры запущен")

        return self

    def _run(self):
        previous_frame_id = None
        previous_check = time.monotonic()

        while not self.stop_event.wait(CHECK_INTERVAL):
            now = time.monotonic()

            try:
                frame_id, _ = self.camera.read()

                # Учитываем время только если между
                # проверками поступили новые кадры.
                if (
                    frame_id is not None
                    and previous_frame_id is not None
                    and frame_id != previous_frame_id
                ):
                    elapsed = min(
                        now - previous_check,
                        CHECK_INTERVAL,
                    )

                    save_uptime(elapsed)

                previous_frame_id = frame_id

            except (OSError, sqlite3.Error) as error:
                print(f"[Uptime] Ошибка: {error}")

            previous_check = now

    def stop(self):
        self.stop_event.set()

        if self.thread is not None:
            self.thread.join(timeout=10)

        print("[Uptime] Мониторинг остановлен")


if __name__ == "__main__":
    init_uptime_db()

    stats = get_uptime_stats()

    print(f"Время наблюдения: {stats['hours']:.2f} ч")
    print(f"Количество измерений: {stats['samples']}")
