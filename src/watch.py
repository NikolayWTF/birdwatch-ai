
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

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
CONFIDENCE = 0.4
IMAGE_SIZE = 640

PRE_RECORD_SECONDS = 5
POST_RECORD_SECONDS = 25
SEGMENT_SECONDS = 2

CONFIRM_FRAMES = 2
RESET_SECONDS = 10
BUFFER_RETENTION = 90

FFMPEG_RECONNECT_DELAY = 5
FFMPEG_STALL_TIMEOUT = 20

RECORDINGS_DIR = Path("recordings/birds")
BUFFER_DIR = Path("recordings/buffer")

RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
BUFFER_DIR.mkdir(parents=True, exist_ok=True)


def start_buffer():
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-timeout", "5000000",
        "-i", RTSP_URL,
        "-map", "0:v:0",
        "-c:v", "copy",
        "-an",
        "-f", "segment",
        "-segment_time", str(SEGMENT_SECONDS),
        "-segment_format", "mpegts",
        "-strftime", "1",
        "-reset_timestamps", "1",
        str(BUFFER_DIR / "%Y%m%d_%H%M%S.ts"),
    ]

    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_buffer(process):
    if process is None or process.poll() is not None:
        return

    process.terminate()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def segment_time(path):
    return datetime.strptime(
        path.stem, "%Y%m%d_%H%M%S"
    )


def get_completed_segments():
    files = sorted(BUFFER_DIR.glob("*.ts"))

    # Последний сегмент может ещё записываться.
    return files[:-1]


def buffer_is_healthy(process, started_at):
    if process is None or process.poll() is not None:
        return False

    segments = list(BUFFER_DIR.glob("*.ts"))

    if not segments:
        return (
            time.monotonic() - started_at
            < FFMPEG_STALL_TIMEOUT
        )

    newest_segment = max(
        segments,
        key=lambda path: path.stat().st_mtime,
    )

    age = time.time() - newest_segment.stat().st_mtime

    return age < FFMPEG_STALL_TIMEOUT


def cleanup_buffer(keep_after):
    for path in get_completed_segments():
        if segment_time(path) < keep_after:
            try:
                path.unlink()
            except OSError:
                pass


def save_recording(event_time):
    start_time = event_time - timedelta(
        seconds=PRE_RECORD_SECONDS
    )
    end_time = event_time + timedelta(
        seconds=POST_RECORD_SECONDS
    )

    segments = [
        path
        for path in get_completed_segments()
        if (
            start_time - timedelta(seconds=SEGMENT_SECONDS * 2)
            <= segment_time(path)
            <= end_time
        )
    ]

    if not segments:
        print("[Recording] Нет видеофрагментов")
        return

    timestamp = event_time.strftime("%Y-%m-%d_%H-%M-%S")
    output_file = RECORDINGS_DIR / f"bird_{timestamp}.mp4"
    playlist = BUFFER_DIR / f"concat_{timestamp}.txt"

    with playlist.open("w", encoding="utf-8") as file:
        for segment in segments:
            file.write(
                f"file '{segment.resolve().as_posix()}'\n"
            )

    offset = max(
        0.0,
        (start_time - segment_time(segments[0])).total_seconds()
    )

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(playlist),
        "-ss", str(offset),
        "-t", str(
            PRE_RECORD_SECONDS + POST_RECORD_SECONDS
        ),
        "-map", "0:v:0",
        "-c:v", "copy",
        "-an",
        "-y",
        str(output_file),
    ]

    try:
        subprocess.run(
            command,
            check=True,
            timeout=60,
        )

        print(
            f"[Recording] Видео сохранено: {output_file}"
        )

    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"[Recording] Ошибка: {error}")
        output_file.unlink(missing_ok=True)

    finally:
        playlist.unlink(missing_ok=True)


def main():
    print("[YOLO] Загрузка модели...")

    model = YOLO(MODEL_PATH)

    bird_class_id = next(
        class_id
        for class_id, name in model.names.items()
        if name == "bird"
    )

    # Удаляем временные сегменты от прошлых запусков.
    for path in BUFFER_DIR.glob("*.ts"):
        path.unlink()

    print("[FFmpeg] Запуск видеобуфера...")

    ffmpeg_process = start_buffer()
    ffmpeg_started_at = time.monotonic()
    last_ffmpeg_restart = 0.0

    camera = None

    try:
        camera = RTSPCamera(
            RTSP_URL,
            reconnect_delay=5,
            timeout_ms=5000,
        ).start()

        print("[BirdWatch] Наблюдение запущено!")
        print("[BirdWatch] Ctrl+C для остановки")

        last_frame_id = None
        consecutive_detections = 0

        recording_event = None
        ready_for_new_event = True
        last_detection_time = None

        while True:
            now = datetime.now()

            # Проверяем, продолжает ли FFmpeg получать видео.
            if not buffer_is_healthy(
                ffmpeg_process,
                ffmpeg_started_at,
            ):
                if (
                    time.monotonic() - last_ffmpeg_restart
                    >= FFMPEG_RECONNECT_DELAY
                ):
                    print(
                        "[FFmpeg] Видеобуфер не обновляется. "
                        "Перезапуск..."
                    )

                    stop_buffer(ffmpeg_process)

                    ffmpeg_process = start_buffer()
                    ffmpeg_started_at = time.monotonic()
                    last_ffmpeg_restart = ffmpeg_started_at

                    print(
                        "[FFmpeg] Повторное подключение запущено"
                    )

            # Проверяем завершение текущего события.
            if recording_event is not None:
                elapsed = (
                    now - recording_event
                ).total_seconds()

                if elapsed >= POST_RECORD_SECONDS + 5:
                    save_recording(recording_event)
                    recording_event = None

            # Удаляем старые сегменты.
            keep_after = now - timedelta(
                seconds=BUFFER_RETENTION
            )

            if recording_event is not None:
                event_start = recording_event - timedelta(
                    seconds=PRE_RECORD_SECONDS + 5
                )
                keep_after = min(keep_after, event_start)

            cleanup_buffer(keep_after)

            frame_id, frame = camera.read()

            if frame is None or frame_id == last_frame_id:
                time.sleep(0.02)
                continue

            last_frame_id = frame_id

            results = model.predict(
                source=frame,
                conf=CONFIDENCE,
                imgsz=IMAGE_SIZE,
                classes=[bird_class_id],
                device="cpu",
                verbose=False,
            )

            bird_found = len(results[0].boxes) > 0
            detection_time = datetime.now()

            if bird_found:
                consecutive_detections += 1
                last_detection_time = detection_time
            else:
                consecutive_detections = 0

            # Разрешаем новое событие после отсутствия птиц.
            if (
                last_detection_time is not None
                and (
                    detection_time - last_detection_time
                ).total_seconds() >= RESET_SECONDS
            ):
                ready_for_new_event = True

            # Проверяем, что буфер уже содержит видео.
            buffer_ready = bool(get_completed_segments())

            # Запускаем новое событие.
            if (
                buffer_ready
                and ready_for_new_event
                and recording_event is None
                and consecutive_detections >= CONFIRM_FRAMES
            ):
                recording_event = detection_time
                ready_for_new_event = False

                print(
                    f"[{detection_time:%H:%M:%S}] "
                    "Обнаружена птица! Записываем видео..."
                )

            time.sleep(0.01)

    finally:
        print("[BirdWatch] Останавливаем систему...")

        if camera is not None:
            camera.stop()

        stop_buffer(ffmpeg_process)

        print("[BirdWatch] Работа завершена")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[BirdWatch] Остановка пользователем")
