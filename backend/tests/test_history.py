import asyncio
import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.history import HistoryCache, aggregate_history, normalize_rows
from app.state import Candle, MarketState
from app.api.routes import candles

async def test_coalescing_and_source_timeframe_isolation():
    calls=[]
    async def fetch(symbol,limit,timeframe):
        calls.append(timeframe);await asyncio.sleep(.01)
        return [(0,1,2,.5,1.5,100)]
    feed=SimpleNamespace(name='binance',fetch_klines=fetch);cache=HistoryCache(capacity=2)
    a,b=await asyncio.gather(cache.get(1,feed,'BTCUSDT','1D',300),cache.get(1,feed,'BTCUSDT','1D',300))
    assert a==b and len(calls)==1
    await cache.get(1,feed,'BTCUSDT','1D',300)
    assert len(calls)==1
    await cache.get(1,feed,'BTCUSDT','4h',300);await cache.get(2,feed,'BTCUSDT','1D',300)
    assert len(calls)==3 and len(cache.cache)==2
    await cache.close()

def test_partial_history_is_not_padded():
    rows=[Candle(0,1,2,.5,1.5,3),Candle(60000,1.5,3,1,2,4)]
    assert aggregate_history(rows,1440)==[dict(t=0,o=1,h=3,l=.5,c=2,v=7)]


def test_history_merges_duplicate_buckets_and_sorts_out_of_order():
    rows = [
        {"t": 120000, "o": 3, "h": 4, "l": 2, "c": 3.5, "v": 2},
        {"t": 0, "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 1},
        {"t": 120000, "o": 3, "h": 5, "l": 2.5, "c": 4.5, "v": 3},
    ]
    assert normalize_rows(rows) == [
        {"t": 0, "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 1},
        {"t": 120000, "o": 3, "h": 5, "l": 2, "c": 4.5, "v": 5},
    ]

async def test_daily_route_and_generation_race():
    state=MarketState();state.feed_mode='bybit';st=state.get('BTCUSDT');st.on_candle(60000,100,110,90,105,20)
    async def fetch(symbol,limit,timeframe):
        assert timeframe=='1D'
        return [(86400000,200,220,190,210,300)]
    stream=SimpleNamespace(feed=SimpleNamespace(name='bybit',fetch_klines=fetch),_generation=1,history=HistoryCache())
    req=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(market_state=state,streamer=stream)))
    data=json.loads((await candles(req,'BTCUSDT',300,'1D')).body)
    assert data['candles'][0]['o']==200 and not data['limitedHistory'] and data['venue']=='BY-F'
    assert st.candles[0].open==100
    stream.feed=SimpleNamespace(name='mock')
    data=json.loads((await candles(req,'BTCUSDT',300,'1D')).body)
    assert data['limitedHistory'] and len(data['candles'])==1
    async def late(*args,**kwargs):
        stream._generation+=1
        return [(0,1,2,.5,1.5,1)]
    stream.feed=SimpleNamespace(name='binance',fetch_klines=late)
    with pytest.raises(HTTPException) as exc:await candles(req,'BTCUSDT',300,'1D')
    assert exc.value.status_code==409
    await stream.history.close()


async def test_candle_route_rejects_stale_instrument_source():
    from fastapi import HTTPException

    state = MarketState(); state.feed_mode = "bybit"; st = state.get("BTCUSDT")
    st.on_candle(60000, 100, 110, 90, 105, 20)
    stream = SimpleNamespace(feed=SimpleNamespace(name="bybit"), _generation=2, history=HistoryCache())
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(market_state=state, streamer=stream)))
    with pytest.raises(HTTPException) as exc:
        await candles(req, "BTCUSDT", 300, "1m", "BI-F:FUTURES:BTCUSDT")
    assert exc.value.status_code == 409
    await stream.history.close()


async def test_candle_route_can_fetch_an_explicit_alternate_source(monkeypatch):
    import app.api.routes as routes

    state = MarketState(); state.feed_mode = "bybit"; st = state.get("BTCUSDT")
    st.on_candle(60000, 100, 110, 90, 105, 20)

    class OtherFeed:
        name = "binance"

        async def fetch_klines(self, symbol, limit=300, timeframe="1m"):
            assert symbol == "BTCUSDT" and timeframe == "1m"
            return [(0, 1, 2, 0.5, 1.5, 3)]

    monkeypatch.setattr(routes, "_history_source_for_venue", lambda venue: OtherFeed())
    stream = SimpleNamespace(feed=SimpleNamespace(name="bybit"), _generation=2, history=HistoryCache())
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(market_state=state, streamer=stream)))
    data = json.loads((await candles(req, "BTCUSDT", 300, "1m", "BI-F:FUTURES:BTCUSDT")).body)
    assert data["venue"] == "BI-F" and data["candles"][0]["o"] == 1 and data["walls"] == []
    assert data["overlaysAvailable"] is False and data["overlaysSource"] == "unavailable_for_selected_source"
    await stream.history.close()

@pytest.mark.parametrize('module_name,cls,param,expected', [('binance','BinanceFuturesFeed','interval','1d'),('bybit','BybitLinearFeed','interval','D'),('okx','OkxSwapFeed','bar','1Dutc')])
async def test_daily_exchange_parameters(monkeypatch,module_name,cls,param,expected):
    import importlib
    module=importlib.import_module('app.feeds.'+module_name);seen={}
    class Response:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def raise_for_status(self):pass
        async def json(self):
            row=['86400000','1','2','.5','1.5','10']
            return [row] if module_name=='binance' else {'retCode':0,'code':'0','result':{'list':[row]},'data':[row]}
    class Session:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def get(self,url,params):seen.update(params);return Response()
    monkeypatch.setattr(module.aiohttp,'ClientSession',Session)
    rows=await getattr(module,cls)(None).fetch_klines('BTCUSDT',timeframe='1D')
    assert seen[param]==expected and rows[0][0]==86400000
