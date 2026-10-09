"""FastAPI application entrypoint.

Wires the ingestion pipeline (feeds → state → analytics → ML scorer) to the
REST API and the throttled WebSocket broadcaster. Also serves the frontend
static build so `uvicorn app.main:app` alone is a complete running terminal.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.alerts import AlertEngine
from app.api import auth as auth_router
from app.api import routes as api_routes
from app.api import alerts_api
from app.config import get_settings
from app.db import init_db, dispose_db
from app.ml_engine import ScalpScorer
from app.services.broadcaster import Broadcaster
from app.services.streamer import MarketStreamer
from app.state import MarketState

logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("scalpair.main")

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    if settings.jwt_secret in ("dev-secret-do-not-use-in-production", "change-me-to-a-long-random-string"):
        log.warning("JWT_SECRET is a default value — set a long random secret before any real deployment")
    if len(settings.jwt_secret) < 32:
        log.warning("JWT_SECRET shorter than 32 bytes — HMAC-SHA256 keys should be >= 32 bytes")

    await init_db(settings.database_url)

    state = MarketState()
    scorer = ScalpScorer(heuristic_weight=settings.ml_heuristic_weight)
    broadcaster = Broadcaster(broadcast_rate=settings.ws_broadcast_rate)
    await broadcaster.connect_redis(settings.redis_url)

    streamer = MarketStreamer(state, settings, scorer, broadcaster.publish)

    alert_engine = AlertEngine(state, settings, scorer)
    alert_task = asyncio.create_task(alert_engine.run(), name="alert-engine")

    dispatch_task = asyncio.create_task(broadcaster.run(), name="broadcaster-dispatch")

    app.state.market_state = state
    app.state.scorer = scorer
    app.state.broadcaster = broadcaster
    app.state.streamer = streamer
    app.state.alert_engine = alert_engine

    streamer_task = asyncio.create_task(streamer.start(), name="streamer-start")

    log.info("Scalpair backend booting — feed=%s top=%d", settings.data_feed, settings.top_symbols)
    try:
        yield
    finally:
        streamer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await streamer_task
        await streamer.stop()
        alert_engine.stop()
        dispatch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dispatch_task
        with contextlib.suppress(asyncio.CancelledError):
            await alert_task
        await dispose_db()
        log.info("Scalpair backend shut down cleanly")


app = FastAPI(
    title="Scalpair API",
    version="0.1.0",
    description="Crypto scalping screener & AI terminal — 100% public read-only market data.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def security_headers(request, call_next):  # noqa: ANN001
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response

app.include_router(api_routes.router)
app.include_router(auth_router.router)
app.include_router(alerts_api.router)


@app.get("/api/v1/meta")
async def meta() -> dict:
    return {
        "name": "Scalpair",
        "version": app.version,
        "description": "Read-only analytical scalping terminal. No keys, no execution, no custody.",
        "safety": "never handles private keys, exchange API secrets, or order execution",
    }


# Serve the SPA from the backend as well (nginx is the primary front in Docker).
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:  # pragma: no cover
    @app.get("/")
    async def root() -> dict:
        return {"service": "Scalpair API", "docs": "/docs"}
