"""Local configuration; model/provider decisions are intentionally not defaulted."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="POLICYPILOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    cache_enabled: Literal[False] = False
    identity_database: Path = Path("data/identity.sqlite3")
    session_ttl_seconds: int = Field(default=3600, ge=60, le=86400, strict=True)

    @field_validator("session_ttl_seconds", mode="before")
    @classmethod
    def parse_session_lifetime(cls, value: object) -> object:
        # Environment variables are strings; do not also accept booleans or floats.
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            return int(value)
        return value
