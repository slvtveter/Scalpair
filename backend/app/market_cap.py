"""Best-effort market-cap enrichment from a public provider.

Market cap is an asset-level value and must never be inferred from futures
volume or price.  The provider is intentionally isolated from the market
stream: a timeout leaves the last known value (or ``None``) in place and does
not affect live prices.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger("scalpair.market_cap")


@dataclass(frozen=True)
class MarketCapRecord:
    asset_id: str
    symbol: str
    market_cap_usd: float
    source: str
    updated_at: float


class MarketCapService:
    """Refreshes CoinGecko's top assets and indexes them by ticker symbol."""

    def __init__(self, url: str, refresh_s: float = 600.0, enabled: bool = True) -> None:
        self.url = url
        self.refresh_s = max(60.0, refresh_s)
        self.enabled = enabled
        self.records: dict[str, MarketCapRecord] = {}
        self.last_attempt_at = 0.0
        self.last_success_at = 0.0
        self.last_error: str | None = None
        self._stop = asyncio.Event()
        self._session: aiohttp.ClientSession | None = None

    @staticmethod
    def _key(symbol: str) -> str:
        value = symbol.upper().replace("-", "")
        for quote in ("USDT", "USDC", "BUSD", "USD"):
            if value.endswith(quote):
                return value[: -len(quote)]
        return value

    @property
    def source(self) -> str:
        return "coingecko"

    async def refresh(self, symbols: list[str]) -> bool:
        if not self.enabled or not symbols:
            return False
        self.last_attempt_at = time.time()
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": "250",
            "page": "1",
            "sparkline": "false",
        }
        try:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=15)
                self._session = aiohttp.ClientSession(timeout=timeout, headers={"accept": "application/json"})
            async with self._session.get(self.url, params=params) as response:
                response.raise_for_status()
                payload: Any = await response.json()
            if not isinstance(payload, list):
                raise ValueError("market-cap provider returned a non-list payload")
            wanted = {self._key(s) for s in symbols}
            fresh: dict[str, MarketCapRecord] = {}
            now = time.time()
            for item in payload:
                if not isinstance(item, dict):
                    continue
                ticker = str(item.get("symbol") or "").upper()
                cap = item.get("market_cap")
                asset_id = str(item.get("id") or "").strip()
                if not ticker or ticker not in wanted or not asset_id:
                    continue
                try:
                    value = float(cap)
                except (TypeError, ValueError):
                    continue
                if value <= 0:
                    continue
                # Results are market-cap sorted; keep the first record for
                # ambiguous tickers rather than silently replacing it.
                fresh.setdefault(ticker, MarketCapRecord(asset_id, ticker, value, self.source, now))
            self.records.update(fresh)
            self.last_success_at = now
            self.last_error = None
            return True
        except Exception as exc:  # noqa: BLE001 — enrichment is non-critical
            self.last_error = str(exc)[:240]
            log.info("market-cap refresh failed: %s", self.last_error)
            return False

    async def run(self, symbols_provider) -> None:  # noqa: ANN001
        if not self.enabled:
            return
        while not self._stop.is_set():
            await self.refresh(list(symbols_provider()))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.refresh_s)
            except asyncio.TimeoutError:
                pass

    async def close(self) -> None:
        self._stop.set()
        if self._session is not None and not self._session.closed:
            await self._session.close()

