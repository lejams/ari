from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="ARI_",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    provider_mode: Literal["fake", "openai"] = "fake"
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'var' / 'ari.db'}"
    prompt_directory: Path = PROJECT_ROOT / "backend" / "src" / "ari" / "prompts"
    frontend_origin: str = "http://localhost:5173"
    enable_mvp_demos: bool = False
    feedback_language: str = "fr-FR"
    application_version: str | None = None

    openai_api_key: str | None = None
    stt_api_key: str | None = None
    stt_base_url: str = "https://api.groq.com/openai/v1"
    stt_model: str = "whisper-large-v3"
    stt_timeout_seconds: float = 30.0
    patient_model: str = "gpt-5.6-luna"
    evaluation_model: str = "gpt-5.6-terra"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    patient_timeout_seconds: float = 30.0
    evaluation_timeout_seconds: float = 60.0
    tts_timeout_seconds: float = 30.0
    audio_sample_rate: int = 24_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
