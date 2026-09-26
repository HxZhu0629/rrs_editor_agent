from threading import Lock
from time import monotonic


class CircuitOpen(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self):
        self.lock = Lock()
        self.failures = 0
        self.retry_at = 0.0
        self.probing = False

    def call(self, operation, threshold=3, cooldown=30):
        with self.lock:
            if self.retry_at:
                if monotonic() < self.retry_at or self.probing:
                    raise CircuitOpen('Dependency circuit is open')
                self.probing = True
        try:
            result = operation()
        except Exception:
            with self.lock:
                self.failures += 1
                if self.failures >= max(1, threshold):
                    self.retry_at = monotonic() + max(1, cooldown)
                self.probing = False
            raise
        else:
            with self.lock:
                self.failures = 0
                self.retry_at = 0.0
                self.probing = False
            return result
