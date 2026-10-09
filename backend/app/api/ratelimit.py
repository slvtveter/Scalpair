"""Lightweight in-memory sliding-window rate limiter (per-IP).

Sufficient for the single-process MVP at the target load (10-20 RPS).
For multi-worker deployments swap for a Redis-backed counter.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def rate_limit(request: Request, limit: int, window_s: float, scope: str) -> None:
    """Reject when the caller exceeds `limit` requests per `window_s` seconds."""
    ip = request.client.host if request.client else "unknown"
    key = f"{scope}:{ip}"
    now = time.monotonic()
    bucket = _BUCKETS[key]
    while bucket and now - bucket[0] > window_s:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate limit exceeded — slow down",
        )
    bucket.append(now)


def auth_rate_limiter(limit: int = 10, window_s: float = 60.0):
    """10 auth attempts per minute per IP — brute-force protection."""

    def _dep(request: Request) -> None:
        rate_limit(request, limit, window_s, scope="auth")

    return _dep
