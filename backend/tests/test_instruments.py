import pytest

from app.instruments import venue_for_feed


@pytest.mark.parametrize('name,expected', [
    ('binance', 'BI-F'), ('bybit', 'BY-F'), ('okx', 'OK-F'),
    ('mock', 'DEMO'), ('', 'UNKNOWN'), ('new-source', 'UNKNOWN'),
    ('binance+bybit-trades', 'UNKNOWN'),
])
def test_source_label_does_not_invent_an_exchange(name, expected):
    assert venue_for_feed(name) == expected
