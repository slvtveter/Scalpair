"""Source labels; never label synthetic or unknown data as an exchange."""


def venue_for_feed(feed_name: str) -> str:
    return {
        "binance": "BI-F",
        "binance_spot": "BI-S",
        "bybit": "BY-F",
        "bybit_spot": "BY-S",
        "okx": "OK-F",
        "mock": "DEMO",
    }.get(feed_name, "UNKNOWN")


def market_for_feed(feed_name: str) -> str:
    return "SPOT" if feed_name in {"binance_spot", "bybit_spot", "okx_spot"} else "FUTURES"
