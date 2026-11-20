import io
import json
import os
import sqlite3
import time
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests
from dotenv import load_dotenv

from event_log import DB_PATH, get_delivery_stats, init_db
from uptime_monitor import get_uptime_stats
from channel_publish import init_publications, publish


load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER_ID = str(os.environ["TELEGRAM_CHAT_ID"])
API_URL = f"https://api.telegram.org/bot{TOKEN}"

session = requests.Session()


MENU = {
    "inline_keyboard": [
        [
            {"text": "📊 Сегодня", "callback_data": "today"},
            {"text": "📈 Неделя", "callback_data": "week"},
        ],
        [
            {"text": "⏱ Мониторинг камеры", "callback_data": "uptime"},
        ],
        [
            {"text": "🎬 Архив видео", "callback_data": "videos:0"},
        ],
        [
            {"text": "🐦 Последнее видео", "callback_data": "last"},
            {"text": "📚 За всё время", "callback_data": "stats"},
        ],
        [
            {"text": "🔄 Главное меню", "callback_data": "menu"},
        ],
        
    ]
}



def api_request(method, data=None, files=None, timeout=30):
    response = session.post(
        f"{API_URL}/{method}",
        data=data,
        files=files,
        timeout=timeout,
    )
    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result.get("description"))

    return result["result"]


def send_message(chat_id, text, show_menu=False):
    data = {"chat_id": chat_id, "text": text}

    if show_menu:
        data["reply_markup"] = json.dumps(
            MENU, ensure_ascii=False
        )

    return api_request("sendMessage", data=data)


def get_rows(query, params=()):
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(query, params).fetchall()


def offer_publication(chat_id, event_id):
    """Показывает кнопку публикации только владельцу."""
    if str(chat_id) != OWNER_ID:
        return

    rows = get_rows(
        """
        SELECT e.id, COALESCE(
            p.status, 'not_published'
        ) AS publication_status
        FROM events e
        LEFT JOIN channel_publications p
            ON p.event_id = e.id
        WHERE e.id = ?
          AND e.status = 'saved'
        """,
        (event_id,),
    )

    if not rows:
        return

    status = rows[0]["publication_status"]

    if status == "published":
        send_message(
            chat_id,
            f"✅ Событие #{event_id} уже опубликовано в канале."
        )
        return

    if status == "publishing":
        send_message(
            chat_id,
            f"⏳ Событие #{event_id} уже имеет статус publishing.\n"
            "Проверь канал перед повторной попыткой."
        )
        return

    keyboard = {
        "inline_keyboard": [[
            {
                "text": "📢 Опубликовать в канале",
                "callback_data": f"publish_ask:{event_id}",
            }
        ]]
    }

    api_request(
        "sendMessage",
        data={
            "chat_id": chat_id,
            "text": (
                f"🐦 Событие #{event_id}\n\n"
                "Это видео ещё не опубликовано в канале."
            ),
            "reply_markup": json.dumps(
                keyboard, ensure_ascii=False
            ),
        },
    )


def confirm_publication(chat_id, event_id):
    """Запрашивает подтверждение публикации."""
    if str(chat_id) != OWNER_ID:
        return

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Опубликовать",
                    "callback_data": f"publish_go:{event_id}",
                },
                {
                    "text": "❌ Отмена",
                    "callback_data": "publish_cancel",
                },
            ]
        ]
    }

    api_request(
        "sendMessage",
        data={
            "chat_id": chat_id,
            "text": (
                f"📢 Публикация события #{event_id}\n\n"
                "Видео появится в публичном канале.\n"
                "Подтвердить?"
            ),
            "reply_markup": json.dumps(
                keyboard, ensure_ascii=False
            ),
        },
    )


def publication_worker(chat_id, event_id):
    """Выполняет отправку, не блокируя Telegram-бота."""
    try:
        publish(event_id)

        message = (
            f"✅ Событие #{event_id} опубликовано в канале!"
        )

    except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
        print(
            f"[Publish] Событие #{event_id}: "
            f"{type(error).__name__}"
        )

        message = (
            f"⚠️ Не удалось завершить публикацию #{event_id}.\n"
            "Проверь канал и статус публикации в SQLite.\n"
            "Не запускай повторную отправку вслепую."
        )

    try:
        # Отдельный HTTP-запрос: не используем session
        # основного polling-потока.
        response = requests.post(
            f"{API_URL}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": message,
            },
            timeout=20,
        )
        response.raise_for_status()

    except requests.RequestException:
        print("[Publish] Не удалось уведомить администратора")



PAGE_SIZE = 5


def show_video_archive(chat_id, page=0, message_id=None):
    page = max(0, page)

    total = get_rows(
        """
        SELECT COUNT(*) AS total
        FROM events
        WHERE status = 'saved'
          AND video_path IS NOT NULL
        """
    )[0]["total"]

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages - 1)

    events = get_rows(
        """
        SELECT id, detected_at
        FROM events
        WHERE status = 'saved'
          AND video_path IS NOT NULL
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (PAGE_SIZE, page * PAGE_SIZE),
    )

    keyboard = []

    for event in events:
        dt = datetime.fromisoformat(event["detected_at"])

        keyboard.append([
            {
                "text": f"🐦 #{event['id']} · {dt:%d.%m %H:%M}",
                "callback_data": f"video:{event['id']}",
            }
        ])

    navigation = []

    if page > 0:
        navigation.append({
            "text": "⬅️ Назад",
            "callback_data": f"videos:{page - 1}",
        })

    if page < total_pages - 1:
        navigation.append({
            "text": "Вперёд ➡️",
            "callback_data": f"videos:{page + 1}",
        })

    if navigation:
        keyboard.append(navigation)

    keyboard.append([
        {"text": "🏠 Главное меню", "callback_data": "menu"}
    ])

    text = (
        "🎬 Архив BirdWatch AI\n\n"
        f"Всего сохранённых видео: {total}\n"
        f"Страница: {page + 1}/{total_pages}\n\n"
        "Выберите запись для просмотра:"
    )

    markup = json.dumps({
        "inline_keyboard": keyboard
    }, ensure_ascii=False)

    if message_id is not None:
        api_request(
            "editMessageText",
            data={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": markup,
            },
        )
    else:
        api_request(
            "sendMessage",
            data={
                "chat_id": chat_id,
                "text": text,
                "reply_markup": markup,
            },
        )



def get_stats(today_only=False):
    condition = ""
    params = ()

    if today_only:
        condition = "WHERE substr(detected_at, 1, 10) = ?"
        params = (date.today().isoformat(),)

    row = get_rows(
        f"""
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(status = 'saved'), 0) AS saved,
            COALESCE(SUM(status = 'failed'), 0) AS failed
        FROM events
        {condition}
        """,
        params,
    )[0]

    return dict(row)


def create_chart(period):
    today = date.today()

    if period == "today":
        rows = get_rows(
            """
            SELECT
                CAST(substr(detected_at, 12, 2) AS INTEGER) AS hour,
                COUNT(*) AS total
            FROM events
            WHERE substr(detected_at, 1, 10) = ?
            GROUP BY hour
            """,
            (today.isoformat(),),
        )

        counts = {row["hour"]: row["total"] for row in rows}
        labels = [f"{hour:02d}" for hour in range(24)]
        values = [counts.get(hour, 0) for hour in range(24)]

        title = f"Активность птиц — {today:%d.%m.%Y}"
        x_label = "Час суток"

    else:
        days = [
            today - timedelta(days=i)
            for i in range(6, -1, -1)
        ]

        rows = get_rows(
            """
            SELECT
                substr(detected_at, 1, 10) AS day,
                COUNT(*) AS total
            FROM events
            WHERE substr(detected_at, 1, 10) BETWEEN ? AND ?
            GROUP BY day
            """,
            (days[0].isoformat(), today.isoformat()),
        )

        counts = {row["day"]: row["total"] for row in rows}
        labels = [day.strftime("%d.%m") for day in days]
        values = [
            counts.get(day.isoformat(), 0)
            for day in days
        ]

        title = "Активность птиц за последние 7 дней"
        x_label = "Дата"

    plt.rcParams["font.family"] = "DejaVu Sans"

    fig, ax = plt.subplots(figsize=(10, 5))

    ax.bar(
        range(len(values)),
        values,
        color="#468F75",
        width=0.75,
    )

    ax.set_title(title, fontsize=16, pad=18)
    ax.set_xlabel(x_label)
    ax.set_ylabel("Количество посещений")

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(
        labels,
        rotation=45 if period == "today" else 0,
    )

    ax.set_ylim(0, max(max(values, default=0) * 1.2, 1))
    ax.grid(axis="y", alpha=0.2)
    ax.set_axisbelow(True)

    fig.tight_layout()

    image = io.BytesIO()
    fig.savefig(image, format="png", dpi=140)
    plt.close(fig)

    image.seek(0)
    return image


def send_chart(chat_id, period):
    image = create_chart(period)

    try:
        caption = (
            "📊 Посещения по часам за сегодня"
            if period == "today"
            else "📈 Посещения за последние 7 дней"
        )

        api_request(
            "sendPhoto",
            data={
                "chat_id": chat_id,
                "caption": caption,
            },
            files={
                "photo": ("statistics.png", image, "image/png"),
            },
            timeout=60,
        )
    finally:
        image.close()


def send_last_video(chat_id, event_id=None):

    if event_id is None:
        rows = get_rows(
            """
            SELECT
                e.id,
                e.detected_at,
                e.video_path,
                o.status AS telegram_status,
                o.telegram_message_id
            FROM events e
            LEFT JOIN telegram_outbox o ON o.event_id = e.id
            WHERE e.status = 'saved'
              AND e.video_path IS NOT NULL
            ORDER BY e.id DESC
            LIMIT 1
            """
        )
    else:
        rows = get_rows(
            """
            SELECT
                e.id,
                e.detected_at,
                e.video_path,
                o.status AS telegram_status,
                o.telegram_message_id
            FROM events e
            LEFT JOIN telegram_outbox o ON o.event_id = e.id
            WHERE e.id = ?
              AND e.status = 'saved'
              AND e.video_path IS NOT NULL
            """,
            (event_id,),
        )


    if not rows:
        send_message(chat_id, "Пока нет сохранённых видео.")
        return

    event = rows[0]

    # Копируем уже отправленное видео через Telegram.
    if (
        event["telegram_status"] == "sent"
        and event["telegram_message_id"]
    ):
        try:
            api_request(
                "copyMessage",
                data={
                    "chat_id": chat_id,
                    "from_chat_id": OWNER_ID,
                    "message_id": event["telegram_message_id"],
                },
            )
            return
        except (requests.RequestException, RuntimeError):
            print("[Bot] Не удалось скопировать сообщение")

    # Если копирование не удалось, загружаем локальный MP4.
    path = Path(event["video_path"])

    if not path.is_file():
        send_message(chat_id, "Видеофайл недоступен на сервере.")
        return

    if path.stat().st_size > 50 * 1024 * 1024:
        send_message(chat_id, "Видео слишком большое для отправки.")
        return

    with path.open("rb") as video:
        api_request(
            "sendVideo",
            data={
                "chat_id": chat_id,
                "caption": f"🐦 Событие #{event['id']}",
                "supports_streaming": "true",
            },
            files={
                "video": (path.name, video, "video/mp4"),
            },
            timeout=150,
        )


def show_menu(chat_id):
    send_message(
        chat_id,
        "🐦 BirdWatch AI\n\n"
        "Система наблюдения за птицами на балконной кормушке.\n\n"
        "Выберите интересующий раздел:",
        show_menu=True,
    )


def handle_action(chat_id, action):


    if action.startswith("publish_ask:"):
        if str(chat_id) != OWNER_ID:
            return

        event_id = int(action.split(":")[1])
        confirm_publication(chat_id, event_id)
        return

    if action.startswith("publish_go:"):
        if str(chat_id) != OWNER_ID:
            return

        event_id = int(action.split(":")[1])

        send_message(
            chat_id,
            f"⏳ Запущена публикация события #{event_id}."
        )

        threading.Thread(
            target=publication_worker,
            args=(chat_id, event_id),
            daemon=True,
        ).start()
        return

    if action == "publish_cancel":
        if str(chat_id) == OWNER_ID:
            send_message(chat_id, "Публикация отменена.")
        return



    if action.startswith("video:"):
        event_id = int(action.split(":")[1])

        send_last_video(
            chat_id,
            event_id=event_id,
        )

        # Публикацию предлагаем только администратору.
        if str(chat_id) == OWNER_ID:
            offer_publication(chat_id, event_id)

        return


    if action.startswith("video:"):
        event_id = int(action.split(":")[1])
        send_last_video(chat_id, event_id=event_id)
        return

    if action == "menu":
        show_menu(chat_id)

    elif action == "today":
        stats = get_stats(today_only=True)

        send_message(
            chat_id,
            "🐦 BirdWatch AI — сегодня\n\n"
            f"Обнаружений: {stats['total']}\n"
            f"Видео записано: {stats['saved']}\n"
            f"Ошибок записи: {stats['failed']}",
        )

        send_chart(chat_id, "today")

    elif action == "week":
        send_chart(chat_id, "week")

    elif action == "stats":
        stats = get_stats()

        send_message(
            chat_id,
            "📚 Статистика за всё время\n\n"
            f"Обнаружений: {stats['total']}\n"
            f"Видео записано: {stats['saved']}\n"
            f"Ошибок записи: {stats['failed']}",
        )

    elif action == "last":
        send_last_video(chat_id)

    elif action == "pending":
        if str(chat_id) != OWNER_ID:
            send_message(
                chat_id,
                "Эта команда доступна только администратору.",
            )
            return

        stats = get_delivery_stats()

        send_message(
            chat_id,
            "📨 Состояние Telegram\n\n"
            f"Отправлено: {stats['sent']}\n"
            f"Ожидает: {stats['pending']}\n"
            f"Отправляется: {stats['sending']}",
        )

    
    elif action == "uptime":
        today = date.today()

        uptime = get_uptime_stats(today.isoformat())
        stats = get_stats(today_only=True)

        hours = uptime["hours"]
        visits = stats["total"]

        if hours > 0:
            visits_per_hour = visits / hours

            message = (
                "⏱ BirdWatch AI — мониторинг\n\n"
                f"📅 {today:%d.%m.%Y}\n"
                f"📹 Время наблюдения: {hours:.2f} ч\n"
                f"🐦 Обнаружений: {visits}\n"
                f"📊 Событий в час: {visits_per_hour:.2f}"
            )

        else:
            message = (
                "⏱ BirdWatch AI — мониторинг\n\n"
                "Пока нет данных о времени работы камеры.\n"
                "Проверка начнётся после запуска "
                "обновлённого watch.py."
            )

        send_message(chat_id, message)



COMMANDS = {
    "/start": "menu",
    "/help": "menu",
    "/menu": "menu",
    "/today": "today",
    "/week": "week",
    "/stats": "stats",
    "/last": "last",
    "/pending": "pending",
    "/videos": "videos:0",
    "/uptime": "uptime",
}


def main():
    init_db()
    init_publications()

    print("[Bot] Telegram-меню запущено")
    print("[Bot] Ctrl+C для остановки")

    offset = None
    last_action = {}

    while True:
        try:
            updates = api_request(
                "getUpdates",
                data={
                    "offset": offset if offset is not None else "",
                    "timeout": 20,
                    "allowed_updates": json.dumps(
                        ["message", "callback_query"]
                    ),
                },
                timeout=30,
            )

            for update in updates:
                offset = update["update_id"] + 1
                callback = update.get("callback_query")

                if callback:
                    chat = callback.get("message", {}).get("chat", {})
                    chat_id = chat.get("id")
                    action = callback.get("data", "")

                    if (
                        action.startswith("videos:")
                        and chat.get("type") == "private"
                        and chat_id
                    ):
                        api_request(
                            "answerCallbackQuery",
                            data={"callback_query_id": callback["id"]},
                        )

                        page = int(action.split(":")[1])

                        show_video_archive(
                            chat_id=chat_id,
                            page=page,
                            message_id=callback["message"]["message_id"],
                        )
                        continue


                    api_request(
                        "answerCallbackQuery",
                        data={
                            "callback_query_id": callback["id"],
                        },
                    )

                else:
                    message = update.get("message", {})
                    chat = message.get("chat", {})
                    chat_id = chat.get("id")

                    text = message.get("text", "")
                    command = text.split(" ", 1)[0].split("@")[0].lower()
                    action = COMMANDS.get(command)

                # Пока принимаем запросы только из личных чатов.
                if (
                    not chat_id
                    or chat.get("type") != "private"
                    or not action
                ):
                    continue

                # Защита от слишком частых запросов.
                now = time.monotonic()


                is_publish_action = (
                    action.startswith("publish_ask:")
                    or action.startswith("publish_go:")
                    or action == "publish_cancel"
                )

                if (
                    not is_publish_action
                    and now - last_action.get(chat_id, 0) < 3
                ):
                    continue

                if not is_publish_action:
                    last_action[chat_id] = now


                last_action[chat_id] = now

                print(f"[Bot] {chat_id}: {action}")
                handle_action(chat_id, action)

        except (
            requests.RequestException,
            RuntimeError,
            sqlite3.Error,
            OSError,
            ValueError,
        ) as error:
            print(
                f"[Bot] Ошибка: {type(error).__name__}: "
                f"{str(error)[:150]}"
            )
            time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Bot] Остановлен")
