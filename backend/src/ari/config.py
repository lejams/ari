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
    voice_transport: Literal["realtime", "pipeline"] = "realtime"
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'var' / 'ari.db'}"
    auto_create_schema: bool = False
    case_directory: Path = PROJECT_ROOT / "cases"
    learning_aid_directory: Path = PROJECT_ROOT / "learning_aids"
    prompt_directory: Path = PROJECT_ROOT / "backend" / "src" / "ari" / "prompts"
    frontend_origin: str = "http://localhost:5173"
    enable_english_technical_test: bool = False
    enable_mvp_demos: bool = False
    feedback_language: str = "fr-FR"
    application_version: str | None = None

    openai_api_key: str | None = None
    openai_realtime_url: str = "wss://api.openai.com/v1/realtime"
    openai_realtime_calls_url: str = "https://api.openai.com/v1/realtime/calls"
    realtime_handshake_timeout_seconds: float = 15.0
    realtime_quality_model: str = "gpt-realtime-2.1"
    realtime_economy_model: str = "gpt-realtime-2.1-mini"
    pipeline_economy_stt_model: str = "gpt-transcribe"
    pipeline_low_latency_stt_model: str = "gpt-live-transcribe"
    stt_handshake_timeout_seconds: float = 10.0
    patient_model: str = "gpt-5.6-luna"
    grounding_model: str = "gpt-5.6-luna"
    evaluation_model: str = "gpt-5.6-terra"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    patient_timeout_seconds: float = 30.0
    evaluation_timeout_seconds: float = 60.0
    tts_timeout_seconds: float = 30.0
    vad_threshold: float = 0.5
    vad_prefix_padding_ms: int = 300
    vad_silence_duration_ms: int = 700
    audio_sample_rate: int = 24_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
