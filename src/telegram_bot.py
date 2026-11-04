
import os
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from event_log import DB_PATH, get_delivery_stats, init_db


load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def api_request(method, data=None, files=None, timeout=20):
    response = requests.post(
        f"{API_URL}/{method}",
        data=data,
        files=files,
        timeout=timeout,
    )
    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))

    return result["result"]


def send_message(text):
    return api_request(
        "sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text,
        },
    )


def get_connection():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def get_statistics(today_only=False):
    query = """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'saved' THEN 1 ELSE 0 END) AS saved,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status = 'recording' THEN 1 ELSE 0 END) AS recording
        FROM events
    """

    params = ()

    if today_only:
        query += " WHERE substr(detected_at, 1, 10) = ?"
        params = (date.today().isoformat(),)

    with get_connection() as connection:
        row = connection.execute(query, params).fetchone()

    return {
        "total": row["total"] or 0,
        "saved": row["saved"] or 0,
        "failed": row["failed"] or 0,
        "recording": row["recording"] or 0,
    }


def handle_today():
    stats = get_statistics(today_only=True)

    send_message(
        "🐦 BirdWatch AI — сегодня\n\n"
        f"📅 {date.today():%d.%m.%Y}\n\n"
        f"Обнаружений: {stats['total']}\n"
        f"Видео сохранено: {stats['saved']}\n"
        f"Ошибок записи: {stats['failed']}\n"
        f"Записываются / не завершены: {stats['recording']}"
    )


def handle_stats():
    stats = get_statistics()
    delivery = get_delivery_stats()

    send_message(
        "📊 BirdWatch AI — общая статистика\n\n"
        f"Обнаружений: {stats['total']}\n"
        f"Видео сохранено: {stats['saved']}\n"
        f"Ошибок записи: {stats['failed']}\n\n"
        "Telegram:\n"
        f"Отправлено: {delivery['sent']}\n"
        f"Ожидает: {delivery['pending']}\n"
        f"Отправляется: {delivery['sending']}"
    )


def handle_pending():
    stats = get_delivery_stats()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT o.event_id, o.attempts, o.last_error
            FROM telegram_outbox o
            WHERE o.status IN ('pending', 'sending')
            ORDER BY o.event_id
            LIMIT 10
            """
        ).fetchall()

    lines = [
        "📨 Очередь Telegram",
        "",
        f"Ожидают: {stats['pending']}",
        f"Отправляются: {stats['sending']}",
        f"Отправлено: {stats['sent']}",
    ]

    if rows:
        lines.append("\nПоследние ожидающие события:")

        for row in rows:
            lines.append(
                f"#{row['event_id']} — попыток: {row['attempts']}"
            )

    send_message("\n".join(lines))


def handle_last():
    with get_connection() as connection:
        event = connection.execute(
            """
            SELECT id, detected_at, confidence, video_path
            FROM events
            WHERE status = 'saved'
              AND video_path IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

        if event is not None:
            delivery = connection.execute(
                """
                SELECT telegram_message_id, status
                FROM telegram_outbox
                WHERE event_id = ?
                """,
                (event["id"],),
            ).fetchone()
        else:
            delivery = None

    if event is None:
        send_message("Пока нет сохранённых видеозаписей.")
        return

    event_id = event["id"]
    detected_at = datetime.fromisoformat(event["detected_at"])

    caption = (
        f"🐦 Последняя запись — событие #{event_id}\n"
        f"🕒 {detected_at:%d.%m.%Y %H:%M:%S}\n"
        f"🎯 Уверенность YOLO: {event['confidence']:.0%}"
    )

    # Если видео уже отправлено, повторно копируем сообщение
    # внутри Telegram, не загружая файл с ноутбука.
    if (
        delivery is not None
        and delivery["status"] == "sent"
        and delivery["telegram_message_id"] is not None
    ):
        try:
            api_request(
                "copyMessage",
                data={
                    "chat_id": CHAT_ID,
                    "from_chat_id": CHAT_ID,
                    "message_id": delivery["telegram_message_id"],
                },
            )
            return

        except (requests.RequestException, RuntimeError):
            print("[Bot] Не удалось скопировать сообщение")

    path = Path(event["video_path"])

    if not path.is_file():
        send_message(f"Видео события #{event_id} не найдено на диске.")
        return

    if path.stat().st_size > 50 * 1024 * 1024:
        send_message("Видео превышает лимит обычной загрузки Bot API.")
        return

    with path.open("rb") as video:
        api_request(
            "sendVideo",
            data={
                "chat_id": CHAT_ID,
                "caption": caption,
                "supports_streaming": "true",
            },
            files={
                "video": (path.name, video, "video/mp4"),
            },
            timeout=120,
        )


def handle_start():
    send_message(
        "🐦 BirdWatch AI\n\n"
        "Система наблюдения за птицами на кормушке.\n\n"
        "Доступные команды:\n"
        "/today — статистика за сегодня\n"
        "/stats — общая статистика\n"
        "/last — последнее видео\n"
        "/pending — очередь отправки\n"
        "/help — список команд"
    )


def handle_command(text):
    command = text.split()[0].split("@")[0].lower()

    if command == "/today":
        handle_today()
    elif command == "/stats":
        handle_stats()
    elif command == "/last":
        handle_last()
    elif command == "/pending":
        handle_pending()
    elif command in ("/start", "/help"):
        handle_start()


def main():
    init_db()

    # Начинаем с последних обновлений, чтобы не выполнять
    # старые команды, накопившиеся до запуска.
    offset = None

    print("[Bot] Получаем начальный offset...")

    updates = api_request(
        "getUpdates",
        data={"timeout": 0},
        timeout=10,
    )

    if updates:
        offset = max(item["update_id"] for item in updates) + 1

    print("[Bot] Обработчик команд запущен")
    print("[Bot] Ctrl+C для остановки")

    while True:
        try:
            params = {
                "timeout": 20,
                "allowed_updates": '["message"]',
            }

            if offset is not None:
                params["offset"] = offset

            updates = api_request(
                "getUpdates",
                data=params,
                timeout=30,
            )

            for update in updates:
                offset = update["update_id"] + 1

                message = update.get("message", {})
                chat = message.get("chat", {})

                # Команды разрешены только владельцу.
                if str(chat.get("id")) != CHAT_ID:
                    continue

                text = message.get("text", "")

                if not text.startswith("/"):
                    continue

                print(f"[Bot] Получена команда: {text}")
                handle_command(text)

        except (requests.RequestException, RuntimeError) as error:
            print(
                f"[Bot] Ошибка связи: {type(error).__name__}. "
                "Повтор через 5 секунд"
            )
            time.sleep(5)

        except (OSError, sqlite3.Error, ValueError) as error:
            print(f"[Bot] Ошибка обработки: {error}")
            time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Bot] Обработчик команд остановлен")
