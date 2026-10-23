
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import cv2
from dotenv import load_dotenv
from ultralytics import YOLO

from rtsp_camera import RTSPCamera


load_dotenv()

CAMERA_IP = os.environ["CAMERA_IP"]
CAMERA_USERNAME = quote(os.environ["CAMERA_USERNAME"], safe="")
CAMERA_PASSWORD = quote(os.environ["CAMERA_PASSWORD"], safe="")

RTSP_URL = (
    f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}"
    f"@{CAMERA_IP}:554/stream1"
)

MODEL_PATH = "yolo26n.pt"
CONFIDENCE = 0.25
IMAGE_SIZE = 640
DISPLAY_WIDTH = 960

SNAPSHOTS_DIR = Path("dataset/detections")
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

print("Загрузка модели YOLO...")

model = YOLO(MODEL_PATH)

bird_class_id = next(
    class_id
    for class_id, name in model.names.items()
    if name == "bird"
)

print(f"Модель загружена. Bird class ID: {bird_class_id}")

camera = RTSPCamera(RTSP_URL).start()

print("Камера подключена!")
print("Q - выход")
print("S - сохранить кадр с обнаружениями")

last_frame_id = None

try:
    while True:
        frame_id, frame = camera.read()

        if frame is None or frame_id == last_frame_id:
            time.sleep(0.01)
            continue

        last_frame_id = frame_id

        start_time = time.perf_counter()

        results = model.predict(
            source=frame,
            conf=CONFIDENCE,
            imgsz=IMAGE_SIZE,
            classes=[bird_class_id],
            device="cpu",
            verbose=False,
        )

        inference_time = time.perf_counter() - start_time

        result = results[0]
        annotated_frame = result.plot()

        bird_count = len(result.boxes)

        cv2.putText(
            annotated_frame,
            f"Birds: {bird_count}",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 0),
            2,
        )

        cv2.putText(
            annotated_frame,
            f"Inference: {inference_time * 1000:.0f} ms",
            (20, 80),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
        )

        height, width = annotated_frame.shape[:2]
        display_height = int(height * DISPLAY_WIDTH / width)

        display_frame = cv2.resize(
            annotated_frame,
            (DISPLAY_WIDTH, display_height),
        )

        cv2.imshow("BirdWatch AI - YOLO", display_frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break

        elif key == ord("s"):
            timestamp = datetime.now().strftime(
                "%Y-%m-%d_%H-%M-%S_%f"
            )

            filename = (
                SNAPSHOTS_DIR / f"detection_{timestamp}.jpg"
            )

            if cv2.imwrite(str(filename), annotated_frame):
                print(f"Кадр сохранён: {filename}")
            else:
                print("Не удалось сохранить кадр")

finally:
    camera.stop()
    cv2.destroyAllWindows()
    print("Соединение закрыто")
