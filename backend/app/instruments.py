"""Source labels; never label synthetic or unknown data as an exchange."""


def venue_for_feed(feed_name: str) -> str:
    return {
        "binance": "BI-F",
        "bybit": "BY-F",
        "okx": "OK-F",
        "mock": "DEMO",
    }.get(feed_name, "UNKNOWN")
