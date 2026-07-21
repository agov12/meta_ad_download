"""Settings and key loading.

API keys come from environment variables / .env (never hardcoded).
Non-secret defaults (model names, default target language) come from
config.yaml at the repo root, overridable via AD_LOCALIZER_CONFIG.
"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Secrets, from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str | None = None       # hosted Whisper (optional if using local)
    anthropic_api_key: str | None = None    # LLM translation (primary)
    deepl_api_key: str | None = None        # translation fallback
    elevenlabs_api_key: str | None = None   # voice clone + TTS
    sync_api_key: str | None = None         # sync.so lipsync
    vozo_api_key: str | None = None         # on-screen text (optional stage)


class AppConfig(BaseModel):
    """Non-secret defaults, from config.yaml."""

    default_target_language: str = "es"
    whisper_model: str = "small"            # faster-whisper model size
    translation_model: str = "claude-sonnet-5"
    elevenlabs_tts_model: str = "eleven_multilingual_v2"
    lipsync_model: str = "lipsync-2"        # cheap default; "sync-3" for highest quality
    work_root: str = "./work"


def load_config(path: Path | None = None) -> AppConfig:
    """Load config.yaml (path override via AD_LOCALIZER_CONFIG). Missing file → defaults."""
    if path is None:
        env_path = os.environ.get("AD_LOCALIZER_CONFIG")
        path = Path(env_path) if env_path else Path("config.yaml")
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
        return AppConfig(**data)
    return AppConfig()


def load_settings() -> Settings:
    return Settings()
