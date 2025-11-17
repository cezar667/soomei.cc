from __future__ import annotations

import threading
import time
from typing import Dict, Tuple

from fastapi import HTTPException, Request


class _RateLimiter:
    def __init__(self) -> None:
        self._hits: Dict[str, Tuple[int, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.time()
        with self._lock:
            count, reset = self._hits.get(key, (0, now + window_seconds))
            if now > reset:
                count = 0
                reset = now + window_seconds
            count += 1
            self._hits[key] = (count, reset)
            if count > limit:
                raise HTTPException(429, "Muitas requisições. Tente novamente em instantes.")


_limiter = _RateLimiter()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def rate_limit_ip(request: Request, scope: str, *, limit: int, window_seconds: int) -> None:
    key = f"{scope}:{_client_ip(request)}"
    _limiter.check(key, limit, window_seconds)
