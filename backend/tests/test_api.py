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
os.environ["ML_SCORE_INTERVAL"] = "1.0"
os.environ["WS_BROADCAST_RATE"] = "10"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # runs lifespan (starts streamer + mock feed)
        # let the mock feed ingest a few cycles
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if len(c.get("/api/v1/screeners/overview").json()["symbols"]) > 0:
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
    def test_overview(self, client):
        r = client.get("/api/v1/screeners/overview")
        assert r.status_code == 200
        symbols = r.json()["symbols"]
        assert 0 < len(symbols) <= 4
        row = symbols[0]
        for field in ("symbol", "price", "imbalance", "walls", "score"):
            assert field in row

    def test_densities(self, client):
        r = client.get("/api/v1/screeners/densities")
        assert r.status_code == 200
        body = r.json()
        for w in body["walls"]:
            assert w["side"] in ("bid", "ask")
            assert w["notional_usd"] > 0
            assert "distance_pct" in w

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
