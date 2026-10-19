from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
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
    content_model: str = "gpt-6-luna"
    content_timeout_seconds: float = 300.0
    content_upload_max_bytes: int = 50 * 1024 * 1024
    worker_poll_seconds: float = 2.0
    worker_id: str | None = None
    # Liveness heartbeat: the worker touches this file on every loop iteration so a container
    # healthcheck can tell a running worker from a wedged one. Unset in local development.
    worker_heartbeat_path: Path | None = None
    # Back-office (separate application, port 8100): where its pages are served from, and
    # the lifetimes of its server-side sessions and one-time invitation links.
    backoffice_origin: str = "http://localhost:8100"
    backoffice_session_days: int = 7
    backoffice_invitation_hours: int = 48
    prompt_directory: Path = PROJECT_ROOT / "backend" / "src" / "ari" / "prompts"
    # Versioned price list: every model call is costed against it when it is recorded.
    pricing_path: Path = PROJECT_ROOT / "backend" / "src" / "ari" / "pricing" / "pricing-v1.yaml"
    frontend_origin: str = "http://localhost:5173"
    feedback_language: str = "fr-FR"
    application_version: str | None = None
    # Emergency kill switch: when true the learner application refuses to start new sessions,
    # practice runs, placement attempts and voice connections; past work stays readable.
    service_paused: bool = False

    # Usage limits are structural: they never ration a normal learner and no euro budget is
    # fixed in advance. Cost goes down through model routing, caching and pre-generation,
    # validated by the evaluation framework, not by cutting sessions.
    # Exam sessions included in the offer per calendar month (UTC).
    exam_sessions_per_month: int = Field(default=2, ge=0)
    # Optional abuse breaker, unset by default: refuses new paid activity once a learner's
    # recorded cost this month reaches it. Calls with an unknown cost are not counted.
    learner_cost_ceiling_eur: float | None = Field(default=None, gt=0)
    # An exam ends at the scenario's duration plus this technical margin; training has no cap.
    session_grace_minutes: int = Field(default=5, ge=0)
    # A voice connection with no activity is closed: an open microphone is billed continuously
    # and a forgotten tab would block the learner's next session.
    voice_idle_timeout_seconds: int = Field(default=300, ge=10)

    @field_validator("learner_cost_ceiling_eur", mode="before")
    @classmethod
    def empty_ceiling_means_off(cls, value: object) -> object:
        """Compose passes an unset variable as an empty string."""
        return None if isinstance(value, str) and not value.strip() else value

    # Learner accounts. `public_url` is the learner domain as visitors type it: links sent by
    # e-mail point there, never at whatever Host header a request carried.
    public_url: str = "http://localhost:8000"
    learner_session_days: int = 30
    learner_activation_hours: int = 48
    learner_reset_minutes: int = 60
    # "log" writes e-mails to the application log instead of sending them (development).
    email_mode: Literal["log", "smtp"] = "log"
    email_from: str = "ARI <no-reply@localhost>"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_timeout_seconds: float = 10.0

    openai_api_key: str | None = None
    stt_api_key: str | None = None
    stt_base_url: str = "https://api.groq.com/openai/v1"
    stt_model: str = "whisper-large-v3"
    stt_timeout_seconds: float = 30.0
    patient_model: str = "gpt-6-luna"
    evaluation_model: str = "gpt-6-luna"
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
