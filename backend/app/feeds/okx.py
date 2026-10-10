"""OKX v5 public feed (third venue: Binance → Bybit → OKX → mock).

Streams (unauthenticated, wss://ws.okx.com:8443/ws/v5/public):
* ``books``      400-depth snapshot + incremental updates (action-based)
* ``trades``     public trades (side = taker side)
* ``tickers``    24h stats (last, open24h, volCcy24h = base units for SWAP)

REST universe: /api/v5/market/tickers?instType=SWAP (instId BTC-USDT-SWAP → BTCUSDT).

Docs: https://www.okx.com/docs-v5/en/#order-book-trading-market-data
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

import aiohttp

from app.feeds.base import FeedConnector

log = logging.getLogger("scalpair.feed.okx")

REST_BASE = "https://www.okx.com"
WS_URL = "wss://ws.okx.com:8443/ws/v5/public"


def inst_to_symbol(inst_id: str) -> str:
    """BTC-USDT-SWAP → BTCUSDT (unified venue-agnostic symbol)."""
    return inst_id.replace("-USDT-SWAP", "USDT")


def symbol_to_inst(symbol: str) -> str:
    return f"{symbol[: -len('USDT')]}-USDT-SWAP"


class OkxSwapFeed(FeedConnector):
    name = "okx"
    ws_url = WS_URL
    # OKX expects a literal "ping" text frame every <30s on public channels
    keepalive_payload = "ping"  # base sends json.dumps — overridden below

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._books: dict[str, dict[str, dict[float, float]]] = {}
        self._book_seq: dict[str, int] = {}
        self._insts: dict[str, str] = {}  # unified symbol -> instId

    async def subscribe_messages(self, symbols: list[str]) -> list[dict[str, Any]]:
        self._insts = {s: symbol_to_inst(s) for s in symbols}
        args = []
        for s in symbols:
            inst = self._insts[s]
            args += [
                {"channel": "books", "instId": inst},
                {"channel": "trades", "instId": inst},
                {"channel": "tickers", "instId": inst},
            ]
        # OKX caps args per request; chunk conservatively
        return [{"op": "subscribe", "args": args[i : i + 20]} for i in range(0, len(args), 20)]

    async def _send_keepalive(self, ws: Any) -> None:
        """OKX uses a literal text 'ping' (not JSON) — base class sends JSON, so override."""
        await ws.send("ping")

    async def fetch_klines(self, symbol: str, limit: int = 300) -> list[tuple]:
        """Backfill 1m OHLCV: /api/v5/market/candles bar=1m -> [(ts,o,h,l,c,vol)]."""
        url = f"{REST_BASE}/api/v5/market/candles"
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(
                url,
                params={"instId": symbol_to_inst(symbol), "bar": "1m", "limit": min(limit, 300)},
            ) as resp:
                resp.raise_for_status()
                body = await resp.json()
        out = []
        for r in body.get("data") or []:  # newest-first: [ts, o, h, l, c, vol, ...]
            try:
                out.append((int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
            except (IndexError, ValueError, TypeError):
                continue
        out.reverse()
        return out

    async def fetch_universe(self, n: int) -> list[dict[str, Any]]:
        url = f"{REST_BASE}/api/v5/market/tickers"
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, params={"instType": "SWAP"}) as resp:
                resp.raise_for_status()
                body = await resp.json()
        rows = body.get("data") or []
        tickers = []
        for row in rows:
            inst = row.get("instId", "")
            if not inst.endswith("-USDT-SWAP"):
                continue
            try:
                last = float(row["last"])
                base_vol = float(row["volCcy24h"])  # base currency units for SWAP
                tickers.append(
                    {
                        "symbol": inst_to_symbol(inst),
                        "last_price": last,
                        "quote_volume_24h": base_vol * last,
                        "price_change_pct_24h": (last / float(row["open24h"]) - 1) * 100.0
                        if float(row.get("open24h") or 0) > 0
                        else 0.0,
                    }
                )
            except (KeyError, ValueError, TypeError, ZeroDivisionError):
                continue
        tickers.sort(key=lambda t: t["quote_volume_24h"], reverse=True)
        return tickers[:n]

    async def handle_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        arg = msg.get("arg") or {}
        channel = arg.get("channel", "")
        inst = arg.get("instId", "")
        sym = self._insts.get(inst, inst_to_symbol(inst) if inst.endswith("-USDT-SWAP") else "")
        data = msg.get("data")
        if not sym or not self._is_tracked(sym):
            return
        if channel == "books":
            await self._on_books(sym, data)
        elif channel == "trades":
            await self._on_trades(sym, data)
        elif channel == "tickers":
            await self._on_ticker(sym, data)

    def _is_tracked(self, symbol: str) -> bool:
        return symbol in self.top_symbols

    async def _on_books(self, sym: str, data: Any) -> None:
        if not isinstance(data, list) or not data:
            return
        book = self._books.setdefault(sym, {"bids": {}, "asks": {}})
        # continuity: update entries carry prevSeqId/seqId; a gap invalidates the
        # maintained book until the next snapshot (OKX sends one on resubscribe)
        if not self._resync_pending.discard if False else True:
            pass
        for entry in data:
            if isinstance(entry, dict) and entry.get("action") == "update":
                try:
                    prev_id, seq_id = int(entry.get("prevSeqId", 0)), int(entry.get("seqId", 0))
                except (TypeError, ValueError):
                    continue
                last = self._book_seq.get(sym)
                if last is not None and prev_id != last:
                    log.warning("okx books seq gap for %s (%d -> %d) — book stale until snapshot",
                                sym, last, seq_id)
                    book["bids"] = {}
                    book["asks"] = {}
                    self._book_seq.pop(sym, None)
                    if self._ws is not None:
                        with contextlib.suppress(Exception):
                            await self._ws.send(json.dumps(
                                {"op": "subscribe", "args": [{"channel": "books", "instId": symbol_to_inst(sym)}]}))
                    return  # drop the corrupted pass; snapshot will rebuild
                self._book_seq[sym] = seq_id
        ts = 0.0
        for entry in data:
            action = entry.get("action", "snapshot")
            ts = max(ts, float(entry.get("ts") or 0))
            if action == "snapshot":
                book["bids"] = {}
                book["asks"] = {}
            for side_key, side_name in (("bids", "bids"), ("asks", "asks")):
                for row in entry.get(side_key, []):
                    try:
                        price, size = float(row[0]), float(row[1])
                    except (IndexError, ValueError, TypeError):
                        continue
                    if size <= 0:
                        book[side_name].pop(price, None)
                    else:
                        book[side_name][price] = size
        bids = sorted(book["bids"].items(), key=lambda x: x[0], reverse=True)[:25]
        asks = sorted(book["asks"].items(), key=lambda x: x[0])[:25]
        await self.callbacks.on_book(sym, bids, asks, ts)

    async def _on_trades(self, sym: str, data: Any) -> None:
        if not isinstance(data, list):
            return
        for tr in data:
            try:
                # OKX v5 sends lowercase taker side ("buy"/"sell")
                taker_buy = tr.get("side", "").lower() == "buy"
                await self.callbacks.on_trade(
                    sym, float(tr["ts"]), float(tr["px"]), float(tr["sz"]), not taker_buy
                )
            except (KeyError, ValueError, TypeError):
                continue

    async def _on_ticker(self, sym: str, data: Any) -> None:
        if not isinstance(data, list) or not data:
            return
        row = data[0]
        try:
            last = float(row["last"])
            open24 = float(row.get("open24h") or 0)
            change = (last / open24 - 1) * 100.0 if open24 > 0 else 0.0
            await self.callbacks.on_ticker(sym, last, change, float(row.get("volCcy24h") or 0) * last)
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            return
