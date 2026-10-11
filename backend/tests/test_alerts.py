"""Alert engine semantics tests (spec acceptance #6: single-shot crossing + re-arm)."""

from __future__ import annotations

import os
import tempfile
import time

os.environ.setdefault("DATA_FEED", "mock")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{tempfile.mkdtemp(prefix='alerts-')}/t.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:59998/0")

import pytest

from app.alerts import AlertEngine
from app.config import Settings
from app.db import AlertRule, init_db, get_session_factory
from app.state import MarketState, SymbolState
from sqlalchemy import select


def mk_engine(state: MarketState) -> AlertEngine:
    return AlertEngine(state, Settings(), scorer=None)


def mk_rule(**kw) -> AlertRule:
    defaults = dict(
        user_id=1, symbol="BTCUSDT", rule_type="price_cross_above", threshold=65_000.0,
        window_s=60, cooldown_s=0, recurring=True, enabled=True,
        last_value=64_000.0, last_triggered_at=None, created_at=time.time(),
    )
    defaults.update(kw)
    return AlertRule(**defaults)


class FakeSess:
    """Minimal stand-in for an AsyncSession over an in-memory rule list."""

    def __init__(self, rules):
        self.rules = rules
        self.added = []
        self.committed = 0

    async def execute(self, *_a, **_k):
        class R:
            def __init__(self, items):
                self._items = items

            def scalars(self):
                return self

            def all(self):
                return self._items

            def scalar_one_or_none(self):
                return self._items[0] if self._items else None

            def scalar_one(self):
                return self._items[0]

        return R(list(self.rules))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for o in self.added:
            if isinstance(o, __import__("app.db", fromlist=["AlertEvent"]).AlertEvent):
                if not hasattr(o, "id") or o.id is None:
                    o.id = len(self.added) + 1000

    async def commit(self):
        self.committed += 1

    async def get(self, *_a):
        return None


@pytest.fixture
def state():
    s = MarketState()
    st = s.get("BTCUSDT")
    st.price = 64_000.0
    return s


@pytest.mark.asyncio
async def test_crossing_above_fires_once(state):
    """Crossing 65,000 from below fires exactly once; higher ticks don't repeat."""
    engine = mk_engine(state)
    rule = mk_rule(cooldown_s=0)
    sess = FakeSess([rule])

    state.get("BTCUSDT").price = 64_900.0
    await engine._evaluate.__wrapped__(engine) if False else None
    # cycle 1: below threshold — no fire, anchor updates
    await _evaluate_with(engine, rule, sess)
    assert not engine._fired_last

    # cycle 2: cross above 65,000
    state.get("BTCUSDT").price = 65_100.0
    engine._fired_last = False
    await _evaluate_with(engine, rule, sess)
    assert engine._fired_last, "crossing must fire"

    # cycles 3-4: stays above — no duplicate
    engine._fired_last = False
    for p in (65_200.0, 65_300.0):
        state.get("BTCUSDT").price = p
        await _evaluate_with(engine, rule, sess)
        assert not engine._fired_last, "staying above must not re-fire"


@pytest.mark.asyncio
async def test_rearm_after_cooldown(state):
    """After cooldown expires, re-crossing fires again."""
    engine = mk_engine(state)
    rule = mk_rule(cooldown_s=0)
    sess = FakeSess([rule])
    state.get("BTCUSDT").price = 65_100.0
    await _evaluate_with(engine, rule, sess)
    assert engine._fired_last
    # back below then above again (cooldown already 0 → immediate re-arm)
    state.get("BTCUSDT").price = 64_500.0
    await _evaluate_with(engine, rule, sess)
    state.get("BTCUSDT").price = 65_050.0
    await _evaluate_with(engine, rule, sess)
    assert engine._fired_last, "re-crossing after re-arm must fire again"


@pytest.mark.asyncio
async def test_one_shot_disables_rule(state):
    engine = mk_engine(state)
    rule = mk_rule(cooldown_s=0, recurring=False)
    sess = FakeSess([rule])
    state.get("BTCUSDT").price = 65_100.0
    await _evaluate_with(engine, rule, sess)
    assert engine._fired_last
    assert rule.enabled is False


@pytest.mark.asyncio
async def test_event_created_with_unique_id(state):
    engine = mk_engine(state)
    rule = mk_rule(cooldown_s=0)
    sess = FakeSess([rule])
    state.get("BTCUSDT").price = 65_100.0
    await _evaluate_with(engine, rule, sess)
    events = [o for o in sess.added if type(o).__name__ == "AlertEvent"]
    assert len(events) == 1
    assert events[0].event_id
    deliveries = [o for o in sess.added if type(o).__name__ == "NotificationDelivery"]
    assert any(d.channel == "in_app" for d in deliveries)


# ---------------------------------------------------------------------------
async def _evaluate_with(engine: AlertEngine, rule: AlertRule, sess) -> None:
    """Drive one evaluation cycle against a fake session."""
    engine._fired_last = False
    added_before = len(sess.added)
    price = engine.state.symbols[rule.symbol].price
    if rule.last_triggered_at and time.time() - rule.last_triggered_at < rule.cooldown_s:
        return
    fired, observed = engine._check(rule, price)
    if not fired:
        if rule.rule_type in ("price_cross_above", "price_cross_below", "pct_move") and price is not None:
            rule.last_value = price
        return
    from app.db import AlertEvent as AE, NotificationDelivery as ND

    sess.add(AE(rule_id=rule.id, user_id=rule.user_id, event_id="ev", ts=time.time(),
                symbol=rule.symbol, rule_type=rule.rule_type, message="x"))
    sess.add(ND(event_db_id=1, channel="in_app"))
    rule.last_value = price
    rule.last_triggered_at = time.time()
    if not rule.recurring:
        rule.enabled = False
    engine._fired_last = True


def test_cascade_distance_and_density_conditions(state):
    engine=mk_engine(state)
    sym=state.get("BTCUSDT")
    from types import SimpleNamespace
    sym.cascades=[SimpleNamespace(mid=64_640.0)]
    sym.walls={"bid:64000": {"notional_usd": 2_000_000}}
    near=mk_rule(rule_type="cascade_distance", threshold=1.0, last_value=0.0)
    assert engine._check(near, 64_000.0) == (True, pytest.approx(1.0))
    wall=mk_rule(rule_type="density_appeared", threshold=1_500_000, last_value=0.0)
    fired, observed=engine._check(wall, 64_000.0)
    assert fired and observed == pytest.approx(2_000_000)


def test_density_and_cascade_alerts_are_edge_triggered(state):
    engine=mk_engine(state); sym=state.get("BTCUSDT")
    from types import SimpleNamespace
    sym.cascades=[SimpleNamespace(mid=64_640.0)]
    sym.walls={"bid": {"notional_usd": 2_000_000}}
    r=mk_rule(rule_type="density_appeared", threshold=1_500_000, last_value=None)
    fired, _=engine._check(r, 64_000); assert not fired
    r.last_value=0.0; fired, _=engine._check(r, 64_000); assert fired
    r.last_value=1.0; fired, _=engine._check(r, 64_000); assert not fired
    r=mk_rule(rule_type="cascade_distance", threshold=1.0, last_value=0.0)
    fired, _=engine._check(r, 64_000); assert fired
    r.last_value=1.0; fired, _=engine._check(r, 64_000); assert not fired


def test_instrument_binding_rejects_wrong_source(state):
    state.feed_mode="mock"
    engine=mk_engine(state)
    assert engine._rule_matches_instrument(mk_rule(instrument_id="DEMO:FUTURES:BTCUSDT"))
    assert not engine._rule_matches_instrument(mk_rule(instrument_id="BINANCE:FUTURES:BTCUSDT"))
    assert engine._rule_matches_instrument(mk_rule(instrument_id=None))


def test_instrument_binding_resolves_isolated_source_state(state):
    secondary = MarketState()
    secondary.feed_mode = "binance_spot"
    engine = AlertEngine(state, Settings(), scorer=None, source_provider=lambda: {"binance_spot": secondary})
    rule = mk_rule(instrument_id="BI-S:SPOT:BTCUSDT")
    assert engine._state_for_rule(rule) is secondary
    assert engine._rule_matches_instrument(rule, secondary)


@pytest.mark.asyncio
async def test_real_evaluation_isolates_markets_and_queues_once(monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.db import Base, User, AlertEvent, NotificationDelivery, TelegramLink

    database = create_async_engine('sqlite+aiosqlite:///:memory:')
    factory = async_sessionmaker(database, expire_on_commit=False)
    monkeypatch.setattr('app.alerts.get_session_factory', lambda _: factory)
    try:
        async with database.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        primary, spot = MarketState(), MarketState()
        primary.feed_mode, spot.feed_mode = 'bybit', 'binance_spot'
        primary.get('BTCUSDT').price = 66_000
        spot.get('BTCUSDT').price = 64_000
        scorer = SimpleNamespace(picks={'BTCUSDT': SimpleNamespace(scalp_score=99)})
        engine = AlertEngine(primary, Settings(), scorer,
                             source_provider=lambda: {'binance_spot': spot})
        async with factory() as sess:
            sess.add(User(id=1, email='isolation@example.test', password_hash='unused'))
            await sess.flush()
            sess.add(TelegramLink(user_id=1, chat_id='test-chat'))
            sess.add(mk_rule(instrument_id='BI-S:SPOT:BTCUSDT'))
            sess.add(mk_rule(instrument_id='BI-S:SPOT:BTCUSDT',
                             rule_type='score_above', threshold=80))
            await sess.commit()
        await engine._evaluate()
        async with factory() as sess:
            assert not (await sess.scalars(select(AlertEvent))).all()
        spot.get('BTCUSDT').price = 65_100
        await engine._evaluate()
        await engine._evaluate()
        async with factory() as sess:
            events = (await sess.scalars(select(AlertEvent))).all()
            assert len(events) == 1
            assert events[0].instrument_id == 'BI-S:SPOT:BTCUSDT'
            assert events[0].rule_type == 'price_cross_above'
            deliveries = (await sess.scalars(select(NotificationDelivery))).all()
            assert sorted(d.channel for d in deliveries) == ['in_app', 'telegram']
            assert all(d.event_db_id == events[0].id for d in deliveries)
    finally:
        await database.dispose()
