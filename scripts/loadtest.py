"""Load test: sustained REST traffic + concurrent WebSocket clients.

Usage (from backend/.venv):
    .venv/bin/python scripts/loadtest.py --rps 20 --duration 60 --ws-clients 25

Verifies the 10-20 RPS target: reports request counts, error rate and latency
percentiles for REST, plus per-client frame delivery for WebSockets.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import httpx
import websockets

BASE = "http://localhost:8080"
WS_URL = "ws://localhost:8080/api/v1/ws/live-feed"

REST_PATHS = [
    "/api/v1/health",
    "/api/v1/screeners/overview",
    "/api/v1/screeners/densities?limit=50",
    "/api/v1/screeners/ai-picks?top=5",
    "/api/v1/markets/symbols",
]


async def rest_worker(client: httpx.AsyncClient, rps: float, duration: float, latencies: list, errors: list) -> None:
    interval = 1.0 / rps
    deadline = time.monotonic() + duration
    i = 0
    while time.monotonic() < deadline:
        started = time.perf_counter()
        path = REST_PATHS[i % len(REST_PATHS)]
        i += 1
        try:
            resp = await client.get(path, timeout=10)
            if resp.status_code != 200:
                errors.append((path, resp.status_code))
        except Exception as exc:  # noqa: BLE001
            errors.append((path, repr(exc)[:80]))
        latencies.append(time.perf_counter() - started)
        await asyncio.sleep(max(0.0, interval - (time.perf_counter() - started)))


async def ws_worker(idx: int, duration: float, frames: list) -> None:
    try:
        async with websockets.connect(WS_URL, open_timeout=10, ping_interval=20) as ws:
            await ws.send(json.dumps({"type": "hello"}))
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                if msg.get("type") == "snapshot":
                    frames.append(msg["ts"])
    except Exception as exc:  # noqa: BLE001
        frames.append(-idx)  # negative marker = client failed


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))] if s else 0.0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rps", type=float, default=20.0)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--ws-clients", type=int, default=25)
    args = ap.parse_args()

    latencies: list[float] = []
    errors: list = []
    async with httpx.AsyncClient(base_url=BASE) as client:
        ws_frames: list = []
        print(f"load test: {args.rps:.0f} rps REST for {args.duration:.0f}s + {args.ws_clients} WS clients")
        results = await asyncio.gather(
            rest_worker(client, args.rps, args.duration, latencies, errors),
            *(ws_worker(i, args.duration, ws_frames) for i in range(args.ws_clients)),
        )

    ok_frames = [f for f in ws_frames if f > 0]
    failed_clients = len([f for f in ws_frames if f <= 0])
    lat_ms = [l * 1000 for l in latencies]
    print(f"\n=== REST ===")
    print(f"requests: {len(latencies)}  errors: {len(errors)} ({len(errors) / max(1, len(latencies)) * 100:.2f}%)")
    print(f"latency ms: p50={pct(lat_ms, 0.5):.1f} p95={pct(lat_ms, 0.95):.1f} p99={pct(lat_ms, 0.99):.1f} max={max(lat_ms, default=0):.1f}")
    if errors:
        print("sample errors:", errors[:5])
    print(f"\n=== WEBSOCKET ({args.ws_clients} clients) ===")
    print(f"clients delivered snapshots: {args.ws_clients - failed_clients}; failed: {failed_clients}")
    if ok_frames:
        per_client = args.ws_clients and len(ok_frames) / max(1, args.ws_clients - failed_clients)
        print(f"snapshot frames delivered total: {len(ok_frames)} (~{per_client:.0f}/client)")
    print("\nVERDICT:", "PASS" if not errors and failed_clients == 0 and pct(lat_ms, 0.95) < 1000 else "INVESTIGATE")


if __name__ == "__main__":
    asyncio.run(main())
