"""Feed package: normalized public market-data connectors.

Every connector speaks the same callback protocol regardless of exchange,
so the streamer orchestrator is exchange-agnostic:

* :class:`app.feeds.binance.BinanceFuturesFeed`  (primary)
* :class:`app.feeds.bybit.BybitLinearFeed`      (fallback)
* :class:`app.feeds.mock.MockFeed`              (offline / demo)
"""
