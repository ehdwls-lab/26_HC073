"""Single acquisition owner, bounded latest cache, fresh production requests."""
import threading
import logging


class FramePump:
    def __init__(self, acquire, preview):
        self.acquire = acquire
        self.preview = preview
        self.stop_event = threading.Event()
        self.condition = threading.Condition()
        self.started = 0
        self.completed = 0
        self.latest = None
        self.error = None
        self.preview_warned = False
        self.thread = threading.Thread(target=self._run, name='orbbec-acquisition', daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        try:
            while not self.stop_event.is_set():
                with self.condition:
                    self.started += 1
                    sequence = self.started
                frame = self.acquire(self.stop_event)
                try:
                    self.preview(frame.color_bgr)
                except Exception:
                    if not self.preview_warned:
                        logging.warning('[UI WARNING] preview unavailable')
                        self.preview_warned = True
                with self.condition:
                    self.latest = frame
                    self.completed = sequence
                    self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.error = exc
                self.condition.notify_all()

    def capture(self):
        with self.condition:
            # Reject both cached and in-flight pre-request acquisitions.
            after = self.started
            self.condition.wait_for(lambda: self.completed > after or self.error is not None
                                    or self.stop_event.is_set())
            if self.stop_event.is_set():
                raise RuntimeError('camera pump stopped')
            if self.error is not None:
                raise self.error
            return self.latest

    def close(self):
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError('camera acquisition did not stop; refusing concurrent pipeline shutdown')
