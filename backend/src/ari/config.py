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
    # PostgreSQL only; the defaults match the docker compose databases. The learner
    # application only ever receives the platform URL; the content pipeline (back-office,
    # worker) needs both.
    database_url: str = "postgresql+psycopg://ari_platform:ari_platform@localhost:5432/ari_platform"
    content_database_url: str = (
        "postgresql+psycopg://ari_content:ari_content@localhost:5432/ari_content"
    )
    content_storage_dir: Path = PROJECT_ROOT / "var" / "content"
    content_model: str = "gpt-5.6-terra"
    content_timeout_seconds: float = 300.0
    content_upload_max_bytes: int = 50 * 1024 * 1024
    worker_poll_seconds: float = 2.0
    worker_id: str | None = None
    # Back-office (separate application, port 8100): where its pages are served from, and
    # the lifetimes of its server-side sessions and one-time invitation links.
    backoffice_origin: str = "http://localhost:8100"
    backoffice_session_days: int = 7
    backoffice_invitation_hours: int = 48
    prompt_directory: Path = PROJECT_ROOT / "backend" / "src" / "ari" / "prompts"
    frontend_origin: str = "http://localhost:5173"
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
    # Exam mode: open microphone, one speech-to-speech model. Set ARI_REALTIME_MODEL to the
    # exact model id you are entitled to; it is pinned on every exam session.
    realtime_model: str = "gpt-realtime-mini"
    realtime_voice: str = "marin"
    realtime_transcription_model: str = "gpt-4o-mini-transcribe"
    realtime_vad_silence_ms: int = 900
    realtime_connect_timeout_seconds: float = 15.0
    audio_sample_rate: int = 24_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
