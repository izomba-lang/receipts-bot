from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_owner_id: int
    anthropic_api_key: str
    anthropic_model_extractor: str
    supabase_url: str
    supabase_service_key: str
    supabase_bucket: str
    log_level: str

    @classmethod
    def from_env(cls) -> Config:
        def _require(key: str) -> str:
            val = os.environ.get(key)
            if not val:
                raise RuntimeError(f"Missing required env var: {key}")
            return val

        return cls(
            telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
            telegram_owner_id=int(_require("TELEGRAM_OWNER_ID")),
            anthropic_api_key=_require("ANTHROPIC_API_KEY"),
            anthropic_model_extractor=os.environ.get(
                "ANTHROPIC_MODEL_EXTRACTOR", "claude-sonnet-4-5"
            ),
            supabase_url=_require("SUPABASE_URL"),
            supabase_service_key=_require("SUPABASE_SERVICE_KEY"),
            supabase_bucket=os.environ.get("SUPABASE_BUCKET", "receipts"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
