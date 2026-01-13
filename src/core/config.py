from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "InfluencerOps Agent"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = True
    log_level: str = "INFO"

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/influencer_ops"
    )
    database_pool_size: int = 5
    database_max_overflow: int = 10

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Gmail OAuth
    gmail_client_id: str = ""
    gmail_client_secret: SecretStr = SecretStr("")
    gmail_redirect_uri: str = "http://localhost:8000/auth/gmail/callback"
    gmail_scopes: list[str] = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
    ]

    # Slack
    slack_bot_token: SecretStr = SecretStr("")
    slack_signing_secret: SecretStr = SecretStr("")
    slack_app_token: SecretStr = SecretStr("")  # For Socket Mode
    slack_channel_id: str = ""  # Channel for notifications/approvals

    # HubSpot
    hubspot_api_key: SecretStr = SecretStr("")
    hubspot_portal_id: str = ""

    # Notion
    notion_api_key: SecretStr = SecretStr("")
    notion_sop_database_id: str = ""

    # LLM (Anthropic Claude)
    anthropic_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-sonnet-4-20250514"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.7

    # OpenAI (for embeddings)
    openai_api_key: SecretStr = SecretStr("")
    embedding_model: str = "text-embedding-3-small"

    # Vector Store
    chroma_persist_directory: str = "./data/chroma"

    # Agent Settings
    agent_mode: Literal["copilot", "autopilot"] = "copilot"
    autopilot_confidence_threshold: float = 0.9
    autopilot_allowed_intents: list[str] = [
        "schedule_meeting",
        "payment_info_collection",
        "remind_pending_reply",
    ]

    # Guardrails
    pii_masking_enabled: bool = True
    max_draft_length: int = 5000
    forbidden_terms: list[str] = []

    # Kill Switch
    kill_switch_key: str = "GLOBAL_STOP"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
