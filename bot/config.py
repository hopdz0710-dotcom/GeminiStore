from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    admin_ids: frozenset[int]
    database_url: str
    djezzy_number: str = "0793555942"

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        database_url = os.getenv("DATABASE_URL", "").strip()
        raw_admins = os.getenv("ADMIN_IDS", "")
        if not token:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN secret.")
        if not database_url:
            raise RuntimeError("Missing runtime-managed DATABASE_URL.")
        try:
            admin_ids = frozenset(int(value.strip()) for value in raw_admins.split(",") if value.strip())
        except ValueError as exc:
            raise RuntimeError("ADMIN_IDS must be comma-separated Telegram numeric IDs.") from exc
        if not admin_ids:
            raise RuntimeError("At least one Telegram administrator ID is required in ADMIN_IDS.")
        return cls(token, admin_ids, database_url)
