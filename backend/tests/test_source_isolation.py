"""Regression checks for source transitions and late exchange responses."""
import asyncio

import pytest

from app.config import Settings
from app.ml_engine import ScalpScorer
from app.services.streamer import MarketStreamer
from app.state import MarketState


class Feed:
    def __init__(self, name):
        self.name = name
        self.stopped = False
        self.callbacks = None

    def stop(self):
        self.stopped = True

    def set_universe(self, symbols):
        self.symbols = symbols


def universe(price):
    return [dict(symbol='BTCUSDT', last_price=price, price_change_pct_24h=1, quote_volume_24h=1000000)]


def streamer():
    return MarketStreamer(MarketState(), Settings(data_feed='auto'), ScalpScorer(), lambda _: None)


async def test_transition_discards_old_history_and_rejects_late_callbacks():
    s = streamer()
    old = Feed('binance')
    s._activate_feed(old, universe(100))
    callbacks = old.callbacks
    await callbacks.on_candle('BTCUSDT', 60000, 100, 110, 90, 105, 12)
    await callbacks.on_book('BTCUSDT', [(100, 20)], [(101, 30)], 1000)
    s.scorer.score_symbol(s.state, 'BTCUSDT', 1000)
    s._seeded_symbols.add('BTCUSDT')
    new = Feed('bybit')
    s._activate_feed(new, universe(200))
    assert old.stopped
    assert not s.scorer.picks
    assert not s._seeded_symbols
    await callbacks.on_candle('BTCUSDT', 120000, 100, 110, 90, 105, 12)
    await callbacks.on_trade('BTCUSDT', 1000, 99, 1, False)
    await callbacks.on_book('BTCUSDT', [(100, 20)], [(101, 30)], 1000)
    await callbacks.on_ticker('BTCUSDT', 99, 1, 1000000)
    await callbacks.on_meta('BTCUSDT', {'funding_rate': 42})
    st = s.state.get('BTCUSDT')
    assert st.price == 200
    assert not st.candles
    assert st.book_ts_ms == 0
    assert st.funding_rate != 42
    await new.callbacks.on_trade('BTCUSDT', 1000, 201, 1, False)
    assert st.price == 201
    assert s.build_snapshot()['symbols'][0]['venue'] == 'BY-F'


async def test_late_rest_backfill_cannot_seed_new_source():
    s = streamer()
    old = Feed('binance')
    started, finish = asyncio.Event(), asyncio.Event()

    async def klines(*args, **kwargs):
        started.set()
        await finish.wait()
        return [(60000, 100, 110, 90, 105, 12)]

    old.fetch_klines = klines
    s._activate_feed(old, universe(100))
    task = asyncio.create_task(s._seed_candles())
    await started.wait()
    s._activate_feed(Feed('bybit'), universe(200))
    finish.set()
    await task
    assert not s.state.get('BTCUSDT').candles
    assert not s._seeded_symbols


def test_empty_recovery_universe_does_not_replace_current_source():
    s = streamer()
    old = Feed('binance')
    s._activate_feed(old, universe(100))
    with pytest.raises(ValueError):
        s._activate_feed(Feed('bybit'), [])
    assert s.feed is old
    assert not old.stopped
    assert s.state.get('BTCUSDT').price == 100


def test_backfill_tracking_is_not_shared_between_streamers():
    a, b = streamer(), streamer()
    a._seeded_symbols.add('BTCUSDT')
    assert not b._seeded_symbols


async def test_degradation_reports_missing_trades_without_mixing_sources():
    s = streamer()
    s._activate_feed(Feed('binance'), universe(100))
    s.DEGRADE_WARMUP_S = .01
    snapshots = []
    s.publish = snapshots.append
    task = asyncio.create_task(s._degradation_watch())
    await asyncio.sleep(0)
    s.book_events = 100
    for _ in range(30):
        await asyncio.sleep(.005)
        if snapshots:
            break
    s._stop.set()
    await task
    assert snapshots[0]['feedDegraded'] is True
    assert s.state.feed_mode == 'binance'
    assert not s.extra_feeds
    assert not s.hybrid_mode


def test_snapshot_24h_volume_is_quote_turnover_not_short_window_volume():
    s = streamer()
    s._activate_feed(Feed('binance'), universe(100))
    st = s.state.get('BTCUSDT')
    st.metrics.volume_1m_usd = 123
    row = s.build_snapshot()['symbols'][0]
    assert row['vol24h'] == 1000000
    assert row['vol1m'] == 123


async def test_catalog_includes_source_and_skips_uninitialized_symbols():
    import json
    from types import SimpleNamespace
    from app.api.routes import symbols

    s = streamer()
    s._activate_feed(Feed('bybit'), universe(100))
    s.top_symbols.append('NOTSEEDEDUSDT')
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(streamer=s, market_state=s.state)))
    data = json.loads((await symbols(request)).body)
    assert data['feed'] == 'bybit'
    assert len(data['symbols']) == 1
    assert data['symbols'][0]['venue'] == 'BY-F'
    assert data['symbols'][0]['market'] == 'FUTURES'


async def test_cascade_loop_uses_closed_candles_and_three_touches(monkeypatch):
    import time
    from app import levels
    from app.levels import Cascade

    s = streamer()
    s._activate_feed(Feed('binance'), universe(100))
    now = int(time.time() * 1000) // 60000 * 60000
    for i in range(11):
        s.state.get('BTCUSDT').on_candle(now - (10-i)*60000, 100, 101, 99, 100, 5)
    seen = {}

    def pivots(candles, k):
        seen['times'] = [c.open_time for c in candles]
        return []

    def cascades(candles, pivots, min_touches):
        seen['touches'] = min_touches
        return [Cascade('support', 99, 99.1, 99.05, 3, now-60000, [0, 3, 6])]

    monkeypatch.setattr(levels, 'detect_pivots', pivots)
    monkeypatch.setattr(levels, 'build_cascades', cascades)
    s.publish = lambda _: s._stop.set()
    s.LEVELS_INTERVAL_S = 0
    await s._levels_loop()
    assert now not in seen['times']
    assert len(seen['times']) == 10
    assert seen['touches'] == 3
    row = s.build_snapshot()['symbols'][0]
    assert row['levels'][0]['timeframe'] == '1m'
    assert row['levels'][0]['touches'] == 3
