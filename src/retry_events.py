
import argparse
import sqlite3
from pathlib import Path

from event_log import DB_PATH, init_db, queue_video


def main():
    parser = argparse.ArgumentParser(
        description="Повторная отправка событий BirdWatch AI"
    )

    parser.add_argument(
        "event_ids",
        nargs="+",
        type=int,
        help="ID событий, которые нужно добавить в очередь",
    )

    args = parser.parse_args()

    init_db()

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    try:
        for event_id in args.event_ids:
            event = connection.execute(
                """
                SELECT id, status, video_path
                FROM events
                WHERE id = ?
                """,
                (event_id,),
            ).fetchone()

            if event is None:
                print(f"[Retry] Событие #{event_id} не найдено")
                continue

            if event["status"] != "saved":
                print(
                    f"[Retry] Событие #{event_id} "
                    f"имеет статус {event['status']}"
                )
                continue

            path = Path(event["video_path"])

            if not path.is_file():
                print(f"[Retry] Файл не найден: {path}")
                continue

            try:
                queue_video(event_id)
                print(
                    f"[Retry] Событие #{event_id} "
                    "добавлено в очередь или уже находится в ней"
                )

            except ValueError as error:
                print(f"[Retry] Ошибка: {error}")

    finally:
        connection.close()


if __name__ == "__main__":
    main()
