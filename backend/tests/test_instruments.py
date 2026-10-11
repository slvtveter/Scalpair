import pytest

from app.instruments import market_for_feed, venue_for_feed
from app.feeds.binance import BinanceSpotFeed
from app.feeds.bybit import BybitSpotFeed


@pytest.mark.parametrize('name,expected', [
    ('binance', 'BI-F'), ('bybit', 'BY-F'), ('okx', 'OK-F'),
    ('binance_spot', 'BI-S'), ('bybit_spot', 'BY-S'),
    ('mock', 'DEMO'), ('', 'UNKNOWN'), ('new-source', 'UNKNOWN'),
    ('binance+bybit-trades', 'UNKNOWN'),
])
def test_source_label_does_not_invent_an_exchange(name, expected):
    assert venue_for_feed(name) == expected


@pytest.mark.parametrize('name,expected', [
    ('binance', 'FUTURES'), ('bybit', 'FUTURES'), ('okx', 'FUTURES'),
    ('binance_spot', 'SPOT'), ('bybit_spot', 'SPOT'), ('mock', 'FUTURES'),
])
def test_market_label_matches_feed(name, expected):
    assert market_for_feed(name) == expected


def test_spot_adapters_have_distinct_market_identity():
    assert (BinanceSpotFeed.name, BinanceSpotFeed.market) == ("binance_spot", "SPOT")
    assert (BybitSpotFeed.name, BybitSpotFeed.market) == ("bybit_spot", "SPOT")
