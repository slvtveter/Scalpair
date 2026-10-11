"""End-to-end API tests over the ASGI transport (mock feed, temp SQLite)."""

from __future__ import annotations

import os
import tempfile
import time

# ---------------------------------------------------------------------------
# Configure environment BEFORE importing the app (get_settings is cached).
# ---------------------------------------------------------------------------
_tmpdir = tempfile.mkdtemp(prefix="scalpair-test-")
os.environ["DATA_FEED"] = "mock"
os.environ["TOP_SYMBOLS"] = "4"
os.environ["REDIS_URL"] = "redis://localhost:59999/0"  # unreachable -> in-memory fallback
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmpdir}/test.db"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["TELEGRAM_BOT_TOKEN"] = "test-bot-token"
os.environ["TELEGRAM_BOT_USERNAME"] = "ScalpairTestBot"
os.environ["ML_SCORE_INTERVAL"] = "1.0"
os.environ["WS_BROADCAST_RATE"] = "10"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db import async_database_url  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # runs lifespan (starts streamer + mock feed)
        # let the mock feed ingest a few cycles
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if (len(c.get("/api/v1/screeners/overview").json()["symbols"]) > 0
                    and c.get("/api/v1/markets/BTCUSDT/candles").json().get("candles")):
                    break
            except Exception:
                pass
            time.sleep(0.5)
        yield c


class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("ok", "degraded")
        assert body["feed_mode"] == "mock"
        assert body["tracked_symbols"] == 4
        assert body["messages_ingested"] > 0

    def test_meta(self, client):
        r = client.get("/api/v1/meta")
        assert r.status_code == 200
        assert "order execution" in r.json()["safety"]


class TestScreeners:
    def test_instrument_catalog_is_source_labelled(self, client):
        r = client.get("/api/v1/instruments")
        assert r.status_code == 200
        body = r.json()
        assert body["instruments"]
        row = body["instruments"][0]
        assert row["instrument_id"].endswith(f":{row['symbol']}")
        assert row["market"] in ("FUTURES", "SPOT")
        assert row["live"] is True

    def test_overview(self, client):
        r = client.get("/api/v1/screeners/overview")
        assert r.status_code == 200
        symbols = r.json()["symbols"]
        assert 0 < len(symbols) <= 4
        row = symbols[0]
        for field in ("symbol", "price", "imbalance", "walls", "score"):
            assert field in row

    def test_overview_uses_prepared_snapshot_payload(self, client):
        streamer = client.app.state.streamer
        assert streamer._last_overview_body
        before = streamer._last_overview_body
        response = client.get("/api/v1/screeners/overview")
        assert response.status_code == 200
        assert response.content == before
        assert streamer._last_overview_body is before

    def test_densities(self, client):
        r = client.get("/api/v1/screeners/densities")
        assert r.status_code == 200
        body = r.json()
        assert body["coverage"]["complete_percent_range"] is False
        for w in body["walls"]:
            assert w["side"] in ("bid", "ask")
            assert w["notional_usd"] > 0
            assert "distance_pct" in w
            assert w["instrument_id"].endswith(f":{w['symbol']}")
            assert w["market"] in ("FUTURES", "SPOT")

    def test_ai_picks(self, client):
        r = client.get("/api/v1/screeners/ai-picks?top=3")
        assert r.status_code == 200
        picks = r.json()["picks"]
        assert len(picks) <= 3
        for p in picks:
            assert 0 <= p["scalp_score"] <= 100
            assert p["tag"]
            scores = [p2["scalp_score"] for p2 in picks]
            assert scores == sorted(scores, reverse=True)


    def test_candles_endpoint(self, client):
        r = client.get("/api/v1/markets/BTCUSDT/candles?limit=50")
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "BTCUSDT"
        assert len(body["candles"]) >= 1
        c = body["candles"][-1]
        for field in ("t", "o", "h", "l", "c", "v"):
            assert field in c
        assert c["h"] >= c["l"]
        for w in body["walls"]:
            assert w["side"] in ("bid", "ask") and w["price"] > 0

    def test_candles_unknown_symbol_404(self, client):
        r = client.get("/api/v1/markets/NOPEUSDT/candles")
        assert r.status_code == 404


class TestAuth:
    def test_postgres_urls_use_async_driver(self):
        assert async_database_url("postgres://user:pass@db/app").startswith("postgresql+asyncpg://")
        assert async_database_url("postgresql://user:pass@db/app").startswith("postgresql+asyncpg://")
        assert async_database_url("sqlite+aiosqlite:///./local.db").startswith("sqlite+")

    def test_register_login_me_flow(self, client):
        email = f"trader{time.time_ns()}@scalpair.dev"
        r = client.post("/api/v1/auth/register", json={"email": email, "password": "s3cretpass!"})
        assert r.status_code == 201, r.text
        user = r.json()
        assert user["subscription_tier"] == "free"

        r = client.post("/api/v1/auth/login", json={"email": email, "password": "s3cretpass!"})
        assert r.status_code == 200
        token = r.json()["access_token"]
        assert r.json()["token_type"] == "bearer"

        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["email"] == email

        headers = {"Authorization": f"Bearer {token}"}
        payload = {"drawings": [{"type": "horizontal", "a": {"t": 1, "p": 100}}]}
        r = client.put("/api/v1/drawings/BTCUSDT", json=payload, headers=headers)
        assert r.status_code == 200
        assert r.json()["drawings"] == payload["drawings"]
        assert client.get("/api/v1/drawings/BTCUSDT", headers=headers).json()["drawings"] == payload["drawings"]
        other = {"drawings": [{"type": "trend", "a": {"t": 1, "p": 2}}]}
        assert client.put("/api/v1/drawings/BTCUSDT?instrument_id=BY-F:FUTURES:BTCUSDT", json=other, headers=headers).status_code == 200
        assert client.get("/api/v1/drawings/BTCUSDT?instrument_id=BY-F:FUTURES:BTCUSDT", headers=headers).json()["drawings"] == other["drawings"]
        assert client.get("/api/v1/drawings/BTCUSDT", headers=headers).json()["drawings"] == payload["drawings"]
        assert client.get("/api/v1/drawings/BTCUSDT").status_code == 401

        favorites = ["BI-S:SPOT:BTCUSDT", "BY-F:FUTURES:BTCUSDT", "ETHUSDT"]
        response = client.put("/api/v1/watchlists", json={"symbols": favorites + favorites}, headers=headers)
        assert response.status_code == 200
        assert response.json()["symbols"] == favorites
        assert client.get("/api/v1/watchlists", headers=headers).json()["symbols"] == favorites
        assert client.get("/api/v1/watchlists").status_code == 401
        invalid = client.put("/api/v1/watchlists", json={"symbols": ["BI-S:FUTURES:BTCUSDT"]}, headers=headers)
        assert invalid.status_code == 422
        assert client.get("/api/v1/watchlists", headers=headers).json()["symbols"] == favorites

        preferences = {
            "focusLayout": 4,
            "focusVenue": "BY-F",
            "syncCrosshair": False,
            "syncRange": True,
            "paneOverlays": {"0": {"walls": False}, "1": {"volume": True}},
            "grid": 3,
            "ignoredServerField": "must not persist",
        }
        r = client.put("/api/v1/preferences", json={"preferences": preferences}, headers=headers)
        assert r.status_code == 200
        saved = r.json()["preferences"]
        assert saved["focusLayout"] == 4
        assert saved["focusVenue"] == "BY-F"
        assert saved["syncCrosshair"] is False
        assert saved["syncRange"] is True
        assert saved["paneOverlays"]["0"]["walls"] is False
        assert "ignoredServerField" not in saved
        assert client.get("/api/v1/preferences", headers=headers).json()["preferences"] == saved

        link_response = client.post("/api/v1/telegram/link", headers=headers)
        assert link_response.status_code == 200
        link_body = link_response.json()
        assert link_body["configured"] is True and link_body["chat_linked"] is False
        token = link_body["link_url"].split("start=", 1)[1]
        webhook = {"message": {"chat": {"id": 424242, "type": "private"}, "text": f"/start {token}"}}
        linked = client.post("/api/v1/telegram/webhook", json=webhook)
        assert linked.status_code == 200 and linked.json()["linked"] is True
        replay = client.post("/api/v1/telegram/webhook", json=webhook)
        assert replay.status_code == 200 and replay.json()["linked"] is False

    def test_duplicate_register_conflict(self, client):
        email = f"dup{time.time_ns()}@scalpair.dev"
        r1 = client.post("/api/v1/auth/register", json={"email": email, "password": "s3cretpass!"})
        assert r1.status_code == 201
        r2 = client.post("/api/v1/auth/register", json={"email": email, "password": "s3cretpass!"})
        assert r2.status_code == 409

    def test_bad_login_401(self, client):
        r = client.post("/api/v1/auth/login", json={"email": "ghost@scalpair.dev", "password": "wrongpass99"})
        assert r.status_code == 401

    def test_short_password_rejected(self, client):
        r = client.post("/api/v1/auth/register", json={"email": f"short{time.time_ns()}@scalpair.dev", "password": "abc"})
        assert r.status_code == 422

    def test_auth_rate_limit_429(self, client):
        """Within one minute a single IP hits the 10-attempt auth budget -> 429.

        Earlier auth tests in this module share the bucket, so we just require
        that the limiter kicks in somewhere within these 11 attempts.
        """
        statuses = []
        for i in range(11):
            email = f"rl{i}-{time.time_ns()}@scalpair.dev"
            r = client.post("/api/v1/auth/register", json={"email": email, "password": "s3cretpass!"})
            statuses.append(r.status_code)
        assert 429 in statuses, f"rate limiter never fired: {statuses}"
        assert all(s in (201, 409, 429) for s in statuses)

    def test_security_headers(self, client):
        r = client.get("/api/v1/health")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"


class TestLiveWebSocket:
    def test_ws_receives_snapshots(self, client):
        with client.websocket_connect("/api/v1/ws/live-feed") as ws:
            ws.send_json({"type": "hello"})
            got_snapshot = False
            deadline = time.time() + 10
            while time.time() < deadline and not got_snapshot:
                msg = ws.receive_json()
                if msg.get("type") == "snapshot":
                    got_snapshot = True
                    assert "symbols" in msg and "picks" in msg and "stats" in msg
            assert got_snapshot, "no snapshot received over websocket"

    def test_ws_invalid_token_closes_politely(self, client):
        """A forged JWT must get a 1008 close frame, not a TCP reset."""
        from starlette.websockets import WebSocketDisconnect as StarletteDisconnect

        with client.websocket_connect("/api/v1/ws/live-feed") as ws:
            ws.send_json({"token": "eyJhbGciOiJIUzI1NiJ9.forged.sig"})
            try:
                while True:
                    msg = ws.receive_json()
                    if msg.get("type") == "snapshot":
                        break
            except StarletteDisconnect as e:
                assert e.code == 1008, f"expected polite 1008 close, got {e.code}"

    def test_ws_garbage_first_message_still_streams(self, client):
        with client.websocket_connect("/api/v1/ws/live-feed") as ws:
            ws.send_text("total garbage not json")
            deadline = time.time() + 10
            got = False
            while time.time() < deadline and not got:
                msg = ws.receive_json()
                if msg.get("type") == "snapshot":
                    got = True
            assert got

    def test_ws_free_tier_trimming(self, client):
        """Free tier sees at most 10 symbols."""
        with client.websocket_connect("/api/v1/ws/live-feed") as ws:
            ws.send_json({})  # anonymous
            deadline = time.time() + 10
            while time.time() < deadline:
                msg = ws.receive_json()
                if msg.get("type") == "snapshot":
                    assert len(msg["symbols"]) <= 10
                    break
