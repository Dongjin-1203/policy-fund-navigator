import time
from typing import Optional

_TTL_SECONDS = 1800  # 30 minutes


class FeedbackCache:
    def __init__(self, ttl: int = _TTL_SECONDS) -> None:
        self._ttl = ttl
        self._store: dict[str, tuple[dict, float]] = {}

    def get(self, key: str) -> Optional[dict]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, ts = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: dict) -> None:
        self._store[key] = (value, time.monotonic())

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


feedback_cache = FeedbackCache()
