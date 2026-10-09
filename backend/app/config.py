"""Central application configuration (loaded from environment / .env)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Feed ---
    data_feed: Literal["auto", "binance", "bybit", "mock"] = "auto"
    top_symbols: int = 30

    # --- Server ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:8080,http://localhost:5173"

    # --- Redis / DB ---
    redis_url: str = "redis://redis:6379/0"
    database_url: str = "sqlite+aiosqlite:///./data/scalpair.db"

    # --- Auth ---
    jwt_secret: str = "dev-secret-do-not-use-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # --- ML engine ---
    ml_score_interval: float = 15.0
    ml_heuristic_weight: float = 0.65
    wall_multiplier: float = 3.0

    # --- Broadcaster ---
    ws_broadcast_rate: float = 6.0  # updates per second to each client

    # --- Logging ---
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
