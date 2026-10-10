"""Bounded, coalesced exchange history requests for chart timeframes."""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

TIMEFRAMES = {'1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1D': 1440}


def aggregate_history(candles, minutes):
    """Aggregate only available source bars; never fill missing historical bars."""
    result = []
    width = minutes * 60000
    for c in candles:
        bucket = c.open_time // width * width
        if not result or result[-1]['t'] != bucket:
            result.append(dict(t=bucket, o=c.open, h=c.high, l=c.low, c=c.close, v=c.volume))
        else:
            row = result[-1]
            row.update(h=max(row['h'], c.high), l=min(row['l'], c.low), c=c.close, v=row['v'] + c.volume)
    return result


class HistoryCache:
    def __init__(self, ttl=15, capacity=128):
        self.ttl = ttl
        self.capacity = capacity
        self.cache = OrderedDict()
        self.pending = {}
        self.gate = asyncio.Semaphore(4)

    async def get(self, generation, feed, symbol, timeframe, limit):
        key = (generation, feed.name, symbol, timeframe, limit)
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < self.ttl:
            self.cache.move_to_end(key)
            return cached[1]
        if key not in self.pending:
            if len(self.pending) >= 32:
                raise RuntimeError("History request queue is full")
            self.pending[key] = asyncio.create_task(self._fetch(key, feed, symbol, timeframe, limit))
        return await asyncio.shield(self.pending[key])

    async def _fetch(self, key, feed, symbol, timeframe, limit):
        try:
            async with self.gate:
                rows = await asyncio.wait_for(feed.fetch_klines(symbol, limit=limit, timeframe=timeframe), timeout=18)
            candles = [dict(t=t, o=o, h=h, l=l, c=c, v=v) for t, o, h, l, c, v in rows]
            self.cache[key] = (time.monotonic(), candles)
            self.cache.move_to_end(key)
            while len(self.cache) > self.capacity:
                self.cache.popitem(last=False)
            return candles
        finally:
            self.pending.pop(key, None)

    async def close(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cache.clear()
