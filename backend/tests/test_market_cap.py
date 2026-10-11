import pytest

from app.market_cap import MarketCapService


def test_asset_key_strips_supported_quotes():
    assert MarketCapService._key("BTCUSDT") == "BTC"
    assert MarketCapService._key("ETH-USDT") == "ETH"
    assert MarketCapService._key("SOLUSD") == "SOL"


@pytest.mark.asyncio
async def test_refresh_keeps_first_provider_ranked_match_for_ambiguous_ticker(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        async def json(self):
            return [
                {"id": "wrapped-bitcoin", "symbol": "btc", "market_cap": 10},
                {"id": "bitcoin", "symbol": "btc", "market_cap": 100},
                {"id": "ethereum", "symbol": "eth", "market_cap": 50},
            ]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Session:
        closed = False

        def __init__(self, **kwargs):
            pass

        def get(self, url, params):
            return Response()

        async def close(self):
            self.closed = True

    monkeypatch.setattr("app.market_cap.aiohttp.ClientSession", Session)
    service = MarketCapService("https://example.invalid", enabled=True)
    assert await service.refresh(["BTCUSDT", "ETHUSDT"])
    assert service.records["BTC"].asset_id == "wrapped-bitcoin"
    assert service.records["BTC"].market_cap_usd == 10
    assert service.records["ETH"].source == "coingecko"
    await service.close()

