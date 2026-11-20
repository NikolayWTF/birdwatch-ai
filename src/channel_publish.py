"""Manual, deduplicated publishing of BirdWatch recordings to a Telegram channel.

Run from project root:
    python src/channel_publish.py check
    python src/channel_publish.py list
    python src/channel_publish.py publish 123
"""

import argparse
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from event_log import DB_PATH, init_db

load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OWNER_ID = os.getenv('TELEGRAM_CHAT_ID')
CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')


def connection():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=10000')
    return db


def init_publications():
    init_db()
    with connection() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS channel_publications (
                event_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                channel_message_id INTEGER,
                published_at TEXT
            )
        ''')



def api(method, data=None, files=None, timeout=30):
    if not TOKEN or not CHANNEL_ID or not OWNER_ID:
        raise ValueError(
            "Проверь TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID "
            "и TELEGRAM_CHANNEL_ID в .env"
        )

    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=data,
        files=files,
        timeout=timeout,
    )

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(
            f"Telegram вернул некорректный ответ: HTTP {response.status_code}"
        )

    if not response.ok or not result.get("ok"):
        description = result.get("description", "Неизвестная ошибка")
        raise RuntimeError(
            f"Telegram API ({method}): "
            f"HTTP {response.status_code} — {description}"
        )

    return result["result"]



def check_channel():
    chat = api('getChat', {'chat_id': CHANNEL_ID})
    me = api('getMe')
    admin = api('getChatMember', {'chat_id': CHANNEL_ID, 'user_id': me['id']})
    print(f"Канал: {chat.get('title', CHANNEL_ID)}")
    print(f"Статус бота: {admin.get('status')}")
    print(f"Право публикации: {admin.get('can_post_messages', False)}")
    if admin.get('status') not in ('administrator', 'creator') or not admin.get('can_post_messages', False):
        raise RuntimeError('Give the bot administrator permission to post messages')


def list_events():
    with connection() as db:
        rows = db.execute('''
            SELECT e.id, e.detected_at, e.video_path,
                   COALESCE(p.status, 'not_published') AS publication
            FROM events e
            LEFT JOIN channel_publications p ON p.event_id=e.id
            WHERE e.status='saved' AND e.video_path IS NOT NULL
            ORDER BY e.id DESC LIMIT 20
        ''').fetchall()
    if not rows:
        print('Нет сохранённых роликов')
    for r in rows:
        print(f"#{r['id']} | {r['detected_at']} | {r['publication']}")


def publish(event_id):
    # Claim before calling Telegram. If the API times out after actually posting,
    # leave the status 'publishing' for manual reconciliation, never auto-duplicate.
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('''
            SELECT e.id, e.detected_at, e.confidence, e.video_path,
                   o.telegram_message_id, o.status AS delivery_status
            FROM events e
            LEFT JOIN telegram_outbox o ON o.event_id=e.id
            WHERE e.id=? AND e.status='saved' AND e.video_path IS NOT NULL
        ''', (event_id,)).fetchone()
        if row is None:
            raise ValueError(f'Сохранённое видео #{event_id} не найдено')
        existing = db.execute(
            'SELECT status FROM channel_publications WHERE event_id=?', (event_id,)
        ).fetchone()
        if existing:
            raise ValueError(f"Событие #{event_id} уже имеет статус {existing['status']}; повторная отправка заблокирована")
        db.execute(
            "INSERT INTO channel_publications(event_id,status) VALUES (?,'publishing')",
            (event_id,),
        )

    event_time = datetime.fromisoformat(row['detected_at'])
    caption = (
        f"🐦 BirdWatch AI · Посещение кормушки\n"
        f"🕒 {event_time:%d.%m.%Y %H:%M}\n"
        f"🎯 Уверенность детекции: {row['confidence']:.0%}\n\n"
        "Больше видео и статистики — в нашем Telegram-боте."
    )
    if row['delivery_status'] == 'sent' and row['telegram_message_id']:
        method = 'copyMessage'
        kwargs = {'data': {
            'chat_id': CHANNEL_ID,
            'from_chat_id': OWNER_ID,
            'message_id': row['telegram_message_id'],
            'caption': caption,
        }}
    else:
        path = Path(row['video_path'])
        if not path.is_file():
            raise FileNotFoundError(f'Видео отсутствует: {path}')
        if path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError('MP4 больше 50 МБ')
        method = 'sendVideo'
        kwargs = {'data': {
            'chat_id': CHANNEL_ID, 'caption': caption,
            'supports_streaming': 'true',
        }}

    if method == 'sendVideo':
        with path.open('rb') as video:
            result = api(method, **kwargs, files={'video': (path.name, video, 'video/mp4')}, timeout=150)
    else:
        result = api(method, **kwargs, timeout=45)

    with connection() as db:
        db.execute('''
            UPDATE channel_publications
            SET status='published', channel_message_id=?,
                published_at=datetime('now') WHERE event_id=?
        ''', (result['message_id'], event_id))
    print(f"Опубликовано событие #{event_id}, сообщение канала #{result['message_id']}")


def main():
    parser = argparse.ArgumentParser(description='BirdWatch AI: публикации в канале')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check', help='Проверить права бота в канале')
    sub.add_parser('list', help='Показать 20 последних видео и статус публикации')
    pub = sub.add_parser('publish', help='Опубликовать выбранное видео')
    pub.add_argument('event_id', type=int)
    args = parser.parse_args()
    init_publications()
    if args.command == 'check':
        check_channel()
    elif args.command == 'list':
        list_events()
    else:
        publish(args.event_id)


if __name__ == '__main__':
    main()
