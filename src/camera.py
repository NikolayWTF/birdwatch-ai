
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import cv2
from dotenv import load_dotenv


load_dotenv()

CAMERA_IP = os.environ["CAMERA_IP"]
CAMERA_USERNAME = quote(os.environ["CAMERA_USERNAME"], safe="")
CAMERA_PASSWORD = quote(os.environ["CAMERA_PASSWORD"], safe="")

RTSP_URL = (
    f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}"
    f"@{CAMERA_IP}:554/stream1"
)

SNAPSHOTS_DIR = Path("dataset/raw")
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)

if not cap.isOpened():
    raise RuntimeError("Не удалось подключиться к камере")

camera_fps = cap.get(cv2.CAP_PROP_FPS)
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

print("Камера подключена!")
print(f"Разрешение: {width}x{height}")
print(f"FPS камеры: {camera_fps}")
print("S - сохранить снимок")
print("Q - выход")

prev_time = time.perf_counter()

try:
    while True:
        ret, frame = cap.read()

        if not ret:
            print("Не удалось получить кадр")
            break

        current_time = time.perf_counter()
        elapsed = current_time - prev_time
        prev_time = current_time

        fps = 1.0 / elapsed if elapsed > 0 else 0.0

        display_frame = frame.copy()

        cv2.putText(
            display_frame,
            f"FPS: {fps:.1f}",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 0),
            2,
        )

        cv2.imshow("BirdWatch AI", display_frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("s"):
            timestamp = datetime.now().strftime(
                "%Y-%m-%d_%H-%M-%S_%f"
            )

            filename = SNAPSHOTS_DIR / f"bird_{timestamp}.jpg"

            if cv2.imwrite(str(filename), frame):
                print(f"Снимок сохранён: {filename}")
            else:
                print("Не удалось сохранить снимок")

        elif key == ord("q"):
            break

finally:
    cap.release()
    cv2.destroyAllWindows()
    print("Соединение закрыто")
