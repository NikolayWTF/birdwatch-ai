
import threading
import time

import cv2


class RTSPCamera:
    def __init__(self, url, reconnect_delay=5, timeout_ms=5000):
        self.url = url
        self.reconnect_delay = reconnect_delay
        self.timeout_ms = timeout_ms

        self.cap = None
        self.frame = None
        self.frame_id = 0

        self.lock = threading.Lock()
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return self

        self.running = True
        self.thread = threading.Thread(
            target=self._update,
            daemon=True,
        )
        self.thread.start()

        return self

    def _connect(self):
        print("[Camera] Подключение к RTSP...")

        cap = cv2.VideoCapture(
            self.url,
            cv2.CAP_FFMPEG,
            [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                self.timeout_ms,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                self.timeout_ms,
            ],
        )

        if not cap.isOpened():
            cap.release()
            return None

        print("[Camera] RTSP подключён!")
        return cap

    def _update(self):
        while self.running:
            try:
                self.cap = self._connect()

                if self.cap is None:
                    print("[Camera] Не удалось подключиться")
                else:
                    while self.running:
                        success, frame = self.cap.read()

                        if not success:
                            print(
                                "[Camera] Поток прервался. "
                                "Переподключение..."
                            )
                            break

                        with self.lock:
                            self.frame = frame
                            self.frame_id += 1

            except Exception as error:
                print(
                    f"[Camera] Ошибка подключения: {error}"
                )

            finally:
                if self.cap is not None:
                    self.cap.release()
                    self.cap = None

                with self.lock:
                    self.frame = None

            if self.running:
                print(
                    f"[Camera] Повторная попытка через "
                    f"{self.reconnect_delay} сек."
                )

                # Event.wait позволяет быстро завершить поток.
                deadline = time.monotonic() + self.reconnect_delay

                while self.running and time.monotonic() < deadline:
                    time.sleep(0.1)

        print("[Camera] Поток чтения завершён")

    def read(self):
        with self.lock:
            if self.frame is None:
                return None, None

            return self.frame_id, self.frame.copy()

    def stop(self):
        self.running = False

        if self.thread is not None:
            self.thread.join(timeout=10)

        # Если чтение ещё блокируется, поток завершится
        # после срабатывания RTSP timeout.
        self.thread = None
