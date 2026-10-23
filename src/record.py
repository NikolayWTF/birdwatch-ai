import os
import subprocess

from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv


load_dotenv()

CAMERA_IP = os.environ["CAMERA_IP"]
CAMERA_USERNAME = quote(
    os.environ["CAMERA_USERNAME"],
    safe=""
)
CAMERA_PASSWORD = quote(
    os.environ["CAMERA_PASSWORD"],
    safe=""
)

RTSP_URL = (
    f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}"
    f"@{CAMERA_IP}:554/stream1"
)

RECORDINGS_DIR = Path("recordings")
RECORDINGS_DIR.mkdir(exist_ok=True)

timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

output_file = RECORDINGS_DIR / f"birdwatch_{timestamp}.mp4"

duration = 6

command = [
    "ffmpeg",
    "-hide_banner",
    "-rtsp_transport", "tcp",
    "-i", RTSP_URL,
    "-t", str(duration),
    "-map", "0:v:0",
    "-map", "0:a:0?",
    "-c:v", "copy",
    "-c:a", "aac",
    "-b:a", "128k",
    "-y",
    str(output_file),
]

print("Начинаю запись...")

try:
    subprocess.run(command, check=True)
except subprocess.CalledProcessError:
    print("Ошибка записи. Проверь подключение к камере.")
    raise

print(f"Видео сохранено: {output_file}")