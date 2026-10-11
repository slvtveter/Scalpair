import pytest

from app.feeds.base import FeedCallbacks
from app.feeds.mock import MockFeed


async def _noop(*_args):
    return None


@pytest.mark.asyncio
async def test_mock_universe_supports_two_hundred_instruments():
    feed = MockFeed(FeedCallbacks(_noop, _noop, _noop, _noop, _noop))
    rows = await feed.fetch_universe(200)
    assert len(rows) == 200
    assert len({row['symbol'] for row in rows}) == 200
    feed.set_universe([row['symbol'] for row in rows])
    assert len(feed.top_symbols) == 200
