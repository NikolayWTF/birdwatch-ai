import threading
import time

import cv2


class RTSPCamera:
    def __init__(self, url):
        self.cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)

        if not self.cap.isOpened():
            raise RuntimeError("Не удалось подключиться к камере")

        self.frame = None
        self.frame_id = 0
        self.lock = threading.Lock()
        self.running = False
        self.thread = None

    def start(self):
        self.running = True

        self.thread = threading.Thread(
            target=self._update,
            daemon=True,
        )

        self.thread.start()
        return self

    def _update(self):
        while self.running:
            success, frame = self.cap.read()

            if not success:
                self.running = False
                break

            with self.lock:
                self.frame = frame
                self.frame_id += 1

    def read(self):
        with self.lock:
            if self.frame is None:
                return None, None

            return self.frame_id, self.frame.copy()

    def stop(self):
        self.running = False

        if self.thread is not None:
            self.thread.join(timeout=2)

        self.cap.release()