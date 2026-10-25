
import os
import threading
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from event_log import (
    claim_next_delivery,
    init_db,
    mark_delivery_failed,
    mark_delivery_sent,
    queue_video,
    reset_interrupted_deliveries,
)


load_dotenv()


class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

        self.enabled = bool(self.token and self.chat_id)

        self.running = False
        self.stop_event = threading.Event()
        self.thread = None

        if self.enabled:
            self.api_url = (
                f"https://api.telegram.org/bot{self.token}"
            )

    def start(self):
        if not self.enabled:
            print("[Telegram] Не настроен")
            return self

        init_db()
        reset_interrupted_deliveries()

        self.running = True
        self.stop_event.clear()

        self.thread = threading.Thread(
            target=self._worker,
            daemon=True,
        )
        self.thread.start()

        print("[Telegram] Надёжная очередь запущена")
        return self

    def send_message(self, text):
        if not self.enabled:
            return

        # Простое оперативное уведомление.
        # Не блокирует основной поток.
        threading.Thread(
            target=self._send_message_safe,
            args=(text,),
            daemon=True,
        ).start()

    def _send_message_safe(self, text):
        try:
            response = requests.post(
                f"{self.api_url}/sendMessage",
                data={
                    "chat_id": self.chat_id,
                    "text": text,
                },
                timeout=15,
            )
            response.raise_for_status()

            data = response.json()
            if not data.get("ok"):
                raise RuntimeError(data.get("description"))

        except (requests.RequestException, ValueError, RuntimeError) as error:
            print(
                f"[Telegram] Не удалось отправить уведомление: "
                f"{type(error).__name__}"
            )

    def send_video(self, event_id):
        # Добавляем событие в постоянную очередь SQLite.
        queue_video(event_id)
        print(f"[Telegram] Событие #{event_id} добавлено в очередь")

    def _send_video(self, delivery):
        path = Path(delivery["video_path"])

        if not path.is_file():
            raise FileNotFoundError(f"Видео не найдено: {path}")

        if path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("Видео превышает лимит 50 МБ")

        event_id = delivery["event_id"]
        detected_at = datetime.fromisoformat(
            delivery["detected_at"]
        )

        caption = (
            f"🐦 BirdWatch AI — событие #{event_id}\n"
            f"🕒 {detected_at:%d.%m.%Y %H:%M:%S}\n"
            f"🎯 Уверенность YOLO: "
            f"{delivery['confidence']:.0%}\n"
            "🎥 Запись посещения кормушки"
        )

        with path.open("rb") as video:
            response = requests.post(
                f"{self.api_url}/sendVideo",
                data={
                    "chat_id": self.chat_id,
                    "caption": caption,
                    "supports_streaming": "true",
                },
                files={
                    "video": (path.name, video, "video/mp4")
                },
                timeout=(15, 120),
            )

        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            raise RuntimeError(data.get("description"))

        return data["result"]["message_id"]

    def _worker(self):
        while not self.stop_event.is_set():
            try:
                delivery = claim_next_delivery()

                if delivery is None:
                    self.stop_event.wait(3)
                    continue

                event_id = delivery["event_id"]
                outbox_id = delivery["outbox_id"]
                attempt = delivery["attempts"] + 1

                print(
                    f"[Telegram] Отправка события #{event_id}, "
                    f"попытка {attempt}"
                )

                try:
                    message_id = self._send_video(delivery)

                    mark_delivery_sent(
                        outbox_id,
                        message_id,
                    )

                    print(
                        f"[Telegram] Событие #{event_id} отправлено!"
                    )

                except (
                    requests.RequestException,
                    OSError,
                    ValueError,
                    RuntimeError,
                    KeyError,
                ) as error:
                    delay = mark_delivery_failed(
                        outbox_id,
                        attempt,
                        error,
                    )

                    print(
                        f"[Telegram] Ошибка события #{event_id}: "
                        f"{type(error).__name__}. "
                        f"Повтор через {delay} сек."
                    )

            except Exception as error:
                print(
                    f"[Telegram] Ошибка обработчика: "
                    f"{type(error).__name__}: {error}"
                )
                self.stop_event.wait(5)

    def stop(self):
        self.stop_event.set()

        if self.thread is not None:
            self.thread.join(timeout=5)

        print("[Telegram] Отправитель остановлен")
