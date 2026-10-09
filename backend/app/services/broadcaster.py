"""WebSocket fan-out hub.

Distributes the latest unified snapshot to all connected frontend clients at a
hard-capped rate (default 6 updates/sec) to prevent UI lag, with per-client
bounded queues (drop-oldest) so a slow client can never back-pressure the
ingestion pipeline. Optionally mirrors snapshots to Redis pub/sub.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("scalpair.broadcaster")


class Broadcaster:
    def __init__(self, broadcast_rate: float = 6.0) -> None:
        self.broadcast_rate = max(0.5, broadcast_rate)
        self.latest: dict[str, Any] | None = None
        self._queues: dict[int, asyncio.Queue] = {}
        self._client_seq = 0
        self._wake = asyncio.Event()
        self._redis = None
        self.redis_connected = False

    # ------------------------------------------------------------------
    # Redis (optional)
    # ------------------------------------------------------------------
    async def connect_redis(self, url: str) -> None:
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
            await self._redis.ping()
            self.redis_connected = True
            log.info("redis connected — mirroring snapshots to pub/sub")
        except Exception as exc:  # noqa: BLE001 — Redis is optional
            self._redis = None
            self.redis_connected = False
            log.warning("redis unavailable (%s) — using in-memory fan-out only", exc)

    # ------------------------------------------------------------------
    # Producer side
    # ------------------------------------------------------------------
    def publish(self, payload: dict[str, Any]) -> None:
        """Set the newest snapshot and wake the dispatch loop (non-blocking)."""
        payload = dict(payload)
        payload["ts"] = payload.get("ts") or int(time.time() * 1000)
        self.latest = payload
        self._wake.set()
        if self._redis is not None and self.redis_connected:
            asyncio.create_task(self._redis_publish(payload))

    async def _redis_publish(self, payload: dict[str, Any]) -> None:
        try:
            await self._redis.publish("scalpair:live-feed", json.dumps(payload, separators=(",", ":")))
        except Exception:  # noqa: BLE001
            self.redis_connected = False

    # ------------------------------------------------------------------
    # Dispatch loop
    # ------------------------------------------------------------------
    async def run(self) -> None:
        interval = 1.0 / self.broadcast_rate
        while True:
            await self._wake.wait()
            self._wake.clear()
            started = time.perf_counter()
            snapshot = self.latest
            if snapshot is not None:
                for q in list(self._queues.values()):
                    if q.full():
                        with contextlib.suppress(asyncio.QueueEmpty):
                            q.get_nowait()  # drop oldest for slow consumers
                    q.put_nowait(snapshot)
            # pace to broadcast_rate regardless of publish frequency
            elapsed = time.perf_counter() - started
            await asyncio.sleep(max(0.0, interval - elapsed))
            if self.latest is not snapshot:
                self._wake.set()

    # ------------------------------------------------------------------
    # Client side
    # ------------------------------------------------------------------
    async def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        self._client_seq += 1
        self._queues[self._client_seq] = q
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        for k, v in list(self._queues.items()):
            if v is q:
                self._queues.pop(k, None)

    @property
    def client_count(self) -> int:
        return len(self._queues)

    async def attach_websocket(self, ws: WebSocket, first_message: dict[str, Any]) -> None:
        """Full client lifecycle: accept, stream snapshots until disconnect."""
        q = await self.register()
        try:
            await ws.send_text(json.dumps(first_message, separators=(",", ":")))
            while True:
                snapshot = await q.get()
                await ws.send_text(json.dumps(snapshot, separators=(",", ":")))
        except Exception:  # noqa: BLE001 — client went away
            pass
        finally:
            self.unregister(q)
            with contextlib.suppress(Exception):
                await ws.close()
