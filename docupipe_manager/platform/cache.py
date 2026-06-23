import threading
import time
import uuid
from collections import OrderedDict


class UserLRUCache:
    """In-process LRU cache with per-entry TTL. Thread-safe."""

    def __init__(self, capacity: int = 1000, ttl_seconds: int = 300):
        self._capacity = capacity
        self._ttl = ttl_seconds
        self._data: OrderedDict[uuid.UUID, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, user_id: uuid.UUID) -> dict | None:
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(user_id)
            if entry is None:
                return None
            ts, value = entry
            if now - ts > self._ttl:
                self._data.pop(user_id, None)
                return None
            self._data.move_to_end(user_id)
            return value

    def set(self, user_id: uuid.UUID, value: dict) -> None:
        now = time.monotonic()
        with self._lock:
            self._data[user_id] = (now, value)
            self._data.move_to_end(user_id)
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def batch_set(self, items: list[tuple[uuid.UUID, dict]]) -> None:
        for uid, value in items:
            self.set(uid, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
