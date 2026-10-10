"""Feed parser + reconnection tests.

Validates that raw exchange frames (Binance & Bybit) are parsed into
normalized callbacks, that malformed frames never raise, and that the
reconnect/backoff machinery survives simulated disconnects.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.feeds.base import FeedCallbacks
from app.feeds.binance import BinanceFuturesFeed
from app.feeds.bybit import BybitLinearFeed
from app.feeds.mock import MockFeed


class Collector:
    """Records every normalized callback invocation."""

    def __init__(self) -> None:
        self.books: list[tuple] = []
        self.trades: list[tuple] = []
        self.candles: list[tuple] = []
        self.tickers: list[tuple] = []
        self.metas: list[tuple] = []

    def callbacks(self) -> FeedCallbacks:
        async def on_book(sym, bids, asks, ts):
            self.books.append((sym, bids, asks, ts))

        async def on_trade(sym, ts, price, qty, is_buyer_maker):
            self.trades.append((sym, ts, price, qty, is_buyer_maker))

        async def on_candle(sym, open_ms, o, h, l, c, v):
            self.candles.append((sym, open_ms, o, h, l, c, v))

        async def on_ticker(sym, last, chg, vol):
            self.tickers.append((sym, last, chg, vol))

        async def on_meta(sym, meta):
            self.metas.append((sym, meta))

        return FeedCallbacks(on_book, on_trade, on_candle, on_ticker, on_meta)


# ---------------------------------------------------------------------------
# Binance parsing
# ---------------------------------------------------------------------------
class TestBinanceParser:
    def make_feed(self, collector: Collector) -> BinanceFuturesFeed:
        feed = BinanceFuturesFeed(collector.callbacks())
        feed.top_symbols = ["BTCUSDT", "ETHUSDT"]
        return feed

    @pytest.mark.asyncio
    async def test_depth_update(self):
        c = Collector()
        feed = self.make_feed(c)
        raw = {
            "stream": "btcusdt@depth20@100ms",
            "data": {
                "e": "depthUpdate", "E": 1720000000000, "s": "BTCUSDT",
                "b": [["68000.0", "1.5"], ["67999.0", "0"]], "a": [["68001.0", "2.0"]],
            },
        }
        await feed.handle_message(raw)
        assert len(c.books) == 1
        sym, bids, asks, ts = c.books[0]
        assert sym == "BTCUSDT"
        assert bids[0] == (68000.0, 1.5)
        assert all(q > 0 for _, q in bids)  # zero levels dropped
        assert asks == [(68001.0, 2.0)]

    @pytest.mark.asyncio
    async def test_agg_trade(self):
        c = Collector()
        feed = self.make_feed(c)
        raw = {"data": {"e": "aggTrade", "s": "BTCUSDT", "p": "68000.5", "q": "0.25", "T": 1720000000000, "m": True}}
        await feed.handle_message(raw)
        assert c.trades[0] == ("BTCUSDT", 1720000000000.0, 68000.5, 0.25, True)

    @pytest.mark.asyncio
    async def test_kline(self):
        c = Collector()
        feed = self.make_feed(c)
        raw = {"data": {"e": "kline", "s": "BTCUSDT", "k": {"t": 1720000000000, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10"}}}
        await feed.handle_message(raw)
        assert c.candles[0][1:] == (1720000000000, 1.0, 2.0, 0.5, 1.5, 10.0)

    @pytest.mark.asyncio
    async def test_ticker_arr(self):
        c = Collector()
        feed = self.make_feed(c)
        raw = {"data": [{"s": "BTCUSDT", "c": "68000", "P": "-1.5", "q": "9999999"}]}
        await feed.handle_message(raw)
        assert c.tickers[0] == ("BTCUSDT", 68000.0, -1.5, 9999999.0)

    @pytest.mark.asyncio
    async def test_untracked_symbol_ignored(self):
        c = Collector()
        feed = self.make_feed(c)
        await feed.handle_message({"data": {"e": "aggTrade", "s": "PEPEUSDT", "p": "1", "q": "1", "T": 1, "m": False}})
        assert not c.trades

    @pytest.mark.asyncio
    async def test_malformed_frames_never_raise(self):
        c = Collector()
        feed = self.make_feed(c)
        for bad in [None, [], "x", {"data": {"e": "aggTrade"}}, {"data": {"e": "depthUpdate", "s": "BTCUSDT", "b": [["bad", "x"]]}}, {"data": 42}]:
            await feed.handle_message(bad)  # must not raise

    @pytest.mark.asyncio
    async def test_subscribe_chunks(self):
        feed = self.make_feed(Collector())
        msgs = await feed.subscribe_messages(["BTCUSDT", "ETHUSDT"])
        streams = [s for m in msgs for s in m["params"]]
        assert "!ticker@arr" in streams
        assert "btcusdt@depth20@100ms" in streams
        assert "ethusdt@aggTrade" in streams


# ---------------------------------------------------------------------------
# Bybit parsing (snapshot + delta book maintenance)
# ---------------------------------------------------------------------------
class TestBybitParser:
    def make_feed(self, collector: Collector) -> BybitLinearFeed:
        feed = BybitLinearFeed(collector.callbacks())
        feed.top_symbols = ["BTCUSDT"]
        return feed

    @pytest.mark.asyncio
    async def test_snapshot_then_delta(self):
        c = Collector()
        feed = self.make_feed(c)
        snap = {"topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": 1,
                "data": {"s": "BTCUSDT", "b": [["100", "5"], ["99", "1"]], "a": [["101", "2"]]}}
        await feed.handle_message(snap)
        sym, bids, asks, _ = c.books[-1]
        assert bids == [(100.0, 5.0), (99.0, 1.0)]

        # delta: remove 99 level, change 100 size, add 98
        delta = {"topic": "orderbook.50.BTCUSDT", "type": "delta", "ts": 2,
                 "data": {"s": "BTCUSDT", "b": [["100", "7"], ["99", "0"], ["98", "3"]], "a": []}}
        await feed.handle_message(delta)
        sym, bids, asks, _ = c.books[-1]
        assert (100.0, 7.0) in bids
        assert all(p != 99.0 for p, _ in bids)
        assert (98.0, 3.0) in bids

    @pytest.mark.asyncio
    async def test_public_trade_taker_side(self):
        c = Collector()
        feed = self.make_feed(c)
        raw = {"topic": "publicTrade.BTCUSDT", "type": "snapshot",
               "data": [{"T": 1720000000000, "s": "BTCUSDT", "S": "Buy", "v": "0.5", "p": "100000"}]}
        await feed.handle_message(raw)
        # S=Buy means the taker bought => buyer is NOT maker
        assert c.trades[0] == ("BTCUSDT", 1720000000000.0, 100000.0, 0.5, False)

    @pytest.mark.asyncio
    async def test_ticker_with_funding_and_oi(self):
        c = Collector()
        feed = self.make_feed(c)
        raw = {"topic": "tickers.BTCUSDT", "type": "snapshot",
               "data": {"symbol": "BTCUSDT", "lastPrice": "68000", "price24hPcnt": "0.02",
                        "turnover24h": "123", "fundingRate": "0.0001", "openInterest": "555"}}
        await feed.handle_message(raw)
        assert c.tickers[0][:3] == ("BTCUSDT", 68000.0, 2.0)
        assert c.metas[0][1] == {"funding_rate": 0.0001, "open_interest": 555.0}

    @pytest.mark.asyncio
    async def test_kline_dict_format(self):
        """Live Bybit kline rows are dicts keyed by field name."""
        c = Collector()
        feed = self.make_feed(c)
        raw = {"topic": "kline.1.BTCUSDT", "type": "snapshot",
               "data": [{"start": 1791508380000, "end": 1791508439999, "interval": "1",
                         "open": "81726.5", "close": "81701", "high": "81726.6",
                         "low": "81694", "volume": "3.404", "confirm": False}]}
        await feed.handle_message(raw)
        assert c.candles[0][1:] == (1791508380000, 81726.5, 81726.6, 81694.0, 81701.0, 3.404)

    @pytest.mark.asyncio
    async def test_ticker_delta_merges_not_overwrites(self):
        """Partial delta frames must not reset known 24h stats to zero."""
        c = Collector()
        feed = self.make_feed(c)
        snap = {"topic": "tickers.BTCUSDT", "type": "snapshot",
                "data": {"symbol": "BTCUSDT", "lastPrice": "68000", "price24hPcnt": "0.02", "turnover24h": "123"}}
        await feed.handle_message(snap)
        delta = {"topic": "tickers.BTCUSDT", "type": "delta",
                 "data": {"symbol": "BTCUSDT", "fundingRate": "0.0002"}}  # no price fields
        await feed.handle_message(delta)
        assert c.tickers[-1] == ("BTCUSDT", 68000.0, 2.0, 123.0)
        assert c.metas[-1][1] == {"funding_rate": 0.0002}

    @pytest.mark.asyncio
    async def test_malformed_frames_never_raise(self):
        c = Collector()
        feed = self.make_feed(c)
        for bad in [None, {"topic": "orderbook.50.BTCUSDT", "data": "notadict"}, {"topic": "x"}]:
            await feed.handle_message(bad)


# ---------------------------------------------------------------------------
# Reconnection / resilience
# ---------------------------------------------------------------------------
class TestResilience:
    @pytest.mark.asyncio
    async def test_stale_watchdog_fires(self):
        """A silent socket must be flagged by the watchdog (forces reconnect)."""
        c = Collector()
        feed = BinanceFuturesFeed(c.callbacks(), stale_timeout=0.2)
        from websockets.exceptions import ConnectionClosed

        with pytest.raises(ConnectionClosed):
            await feed._stale_watchdog(lambda: time.time() - 10)  # last msg 10s ago

    @pytest.mark.asyncio
    async def test_stale_watchdog_silent_when_active(self):
        c = Collector()
        feed = BinanceFuturesFeed(c.callbacks(), stale_timeout=5.0)
        task = asyncio.create_task(feed._stale_watchdog(lambda: time.time()))
        await asyncio.sleep(0.3)
        assert not task.done()
        task.cancel()

    @pytest.mark.asyncio
    async def test_mock_feed_emits_all_event_types(self):
        c = Collector()
        feed = MockFeed(c.callbacks())
        feed.set_universe(["BTCUSDT", "ETHUSDT"])
        task = asyncio.create_task(feed.run())
        await asyncio.sleep(1.5)
        feed.stop()
        try:
            await asyncio.wait_for(task, timeout=3)
        except asyncio.TimeoutError:
            task.cancel()
        assert c.books, "mock feed produced no book events"
        assert c.trades, "mock feed produced no trade events"
        assert c.candles, "mock feed produced no candle events"
        assert c.tickers, "mock feed produced no ticker events"


# ---------------------------------------------------------------------------
# OKX parsing
# ---------------------------------------------------------------------------
from app.feeds.okx import OkxSwapFeed, inst_to_symbol  # noqa: E402


class TestOkxParser:
    def make_feed(self, collector: Collector) -> OkxSwapFeed:
        feed = OkxSwapFeed(collector.callbacks())
        feed.top_symbols = ["BTCUSDT"]
        feed._insts = {"BTC-USDT-SWAP": "BTCUSDT"}
        return feed

    @pytest.mark.asyncio
    async def test_books_snapshot_then_update(self):
        c = Collector()
        feed = self.make_feed(c)
        snap = {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
                "data": [{"action": "snapshot", "ts": 1000,
                          "bids": [["100", "5"], ["99", "1"]], "asks": [["101", "2"]]}]}
        await feed.handle_message(snap)
        sym, bids, asks, _ = c.books[-1]
        assert sym == "BTCUSDT" and bids[0] == (100.0, 5.0) and asks == [(101.0, 2.0)]
        upd = {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
               "data": [{"action": "update", "ts": 2000,
                         "bids": [["100", "0"], ["98", "7"]], "asks": [["101", "3"]]}]}
        await feed.handle_message(upd)
        sym, bids, asks, _ = c.books[-1]
        assert (100.0, 5.0) not in bids  # zero size removes
        assert (98.0, 7.0) in bids and asks == [(101.0, 3.0)]

    @pytest.mark.asyncio
    async def test_trades_taker_side(self):
        c = Collector()
        feed = self.make_feed(c)
        raw = {"arg": {"channel": "trades", "instId": "BTC-USDT-SWAP"},
               "data": [{"instId": "BTC-USDT-SWAP", "ts": "1720000000000", "px": "100", "sz": "1", "side": "Buy"}]}
        await feed.handle_message(raw)
        assert c.trades[0] == ("BTCUSDT", 1720000000000.0, 100.0, 1.0, False)

    @pytest.mark.asyncio
    async def test_ticker_quote_volume(self):
        c = Collector()
        feed = self.make_feed(c)
        raw = {"arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
               "data": [{"last": "100", "open24h": "90", "volCcy24h": "1000"}]}
        await feed.handle_message(raw)
        assert c.tickers[0] == ("BTCUSDT", 100.0, pytest.approx(11.11, abs=0.01), 100000.0)

    def test_inst_mapping(self):
        assert inst_to_symbol("BTC-USDT-SWAP") == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_malformed_never_raise(self):
        c = Collector()
        feed = self.make_feed(c)
        for bad in [None, {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "data": "x"}, {"arg": {}}, {}]:
            await feed.handle_message(bad)
