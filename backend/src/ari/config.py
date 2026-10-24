import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from ari.domain.clinical import PERSONA_VARIANTS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Only bracketed ElevenLabs audio tags ("[nervous] [sighs]"): never words, so the authored
# clinical phrases are still exactly what the patient says.
AUDIO_TAGS = r"^(\[[a-z][a-z ]{0,29}\]\s*){0,4}$"


class ElevenLabsVoiceSettings(BaseModel):
    """ElevenLabs voice settings; unset keeps the voice's own."""

    model_config = ConfigDict(extra="forbid")

    stability: float | None = Field(default=None, ge=0, le=1)
    similarity_boost: float | None = Field(default=None, ge=0, le=1)
    style: float | None = Field(default=None, ge=0, le=1)
    speed: float | None = Field(default=None, ge=0.7, le=1.2)


class ExamPersonaVoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: str | None = None
    tags: Annotated[str, Field(pattern=AUDIO_TAGS)] = ""
    voice_settings: ElevenLabsVoiceSettings = Field(default_factory=ElevenLabsVoiceSettings)


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
    pricing_path: Path = PROJECT_ROOT / "backend" / "src" / "ari" / "pricing" / "pricing-v2.yaml"
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
    # Provider of each LLM task (infrastructure/providers/llm_routing.py). The model of a task
    # must be one of its provider's: ARI_EVALUATION_PROVIDER=anthropic goes with
    # ARI_EVALUATION_MODEL=claude-sonnet-5-5. Unset, the exam fact audit follows the patient.
    patient_provider: Literal["openai", "anthropic"] = "openai"
    evaluation_provider: Literal["openai", "anthropic"] = "openai"
    content_provider: Literal["openai", "anthropic"] = "openai"
    fact_attribution_provider: Literal["openai", "anthropic"] | None = None
    fact_attribution_model: str | None = None
    anthropic_api_key: str | None = None
    anthropic_max_tokens: int = Field(default=16000, ge=1024)
    # None keeps the model's own default effort (high on Claude Sonnet 5.5).
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None

    @field_validator(
        "fact_attribution_provider", "fact_attribution_model", "anthropic_effort", mode="before"
    )
    @classmethod
    def empty_means_unset(cls, value: object) -> object:
        """Compose passes an unset variable as an empty string."""
        return None if isinstance(value, str) and not value.strip() else value

    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    # Training voice. "openai" uses tts_model and tts_voice above (gpt-4o-mini-tts is retired on
    # 6 January 2027); "elevenlabs" uses the settings below (docs/spikes/exam-elevenlabs.md).
    tts_provider: Literal["openai", "elevenlabs"] = "openai"
    elevenlabs_api_key: str | None = None
    # Chosen by the owner on 22 October 2026 (plan, section 6, point 2).
    elevenlabs_voice_id: str | None = "3nMIMZ7RlGwsq1WLgxY3"
    elevenlabs_tts_model: str = "eleven_v4_turbo"
    elevenlabs_output_format: str = "pcm_24000"  # the only format the players accept
    elevenlabs_base_url: str = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
    # How a voice is registered on a new connection: by id, the shape the service accepts
    # (first live call, 24 October 2026); "objects" is refused with a validation error.
    elevenlabs_voice_registration: Literal["objects", "ids"] = "ids"
    patient_timeout_seconds: float = 30.0
    evaluation_timeout_seconds: float = 60.0
    tts_timeout_seconds: float = 30.0
    # Exam mode: open microphone, one speech-to-speech model. Set ARI_REALTIME_MODEL to the
    # exact model id you are entitled to; it is pinned on every exam session.
    # Exam engine: "openai_realtime" (one speech-to-speech model, the default until the chain
    # is validated) or "chain" (Whisper, patient fact selection, ElevenLabs voice per persona;
    # application/services/exam_session.py). Sessions keep the engine they were created with.
    exam_engine: Literal["openai_realtime", "chain"] = "openai_realtime"
    elevenlabs_exam_tts_model: str = "eleven_v4_turbo"
    # How each persona sounds (plan, 6.2: the owner's choice), as JSON, e.g.
    # {"anxious": {"voice_id": "<id>", "tags": "[nervous]",
    #              "voice_settings": {"stability": 0.3, "style": 0.6, "speed": 1.1}}}.
    # Three levers: the voice, its settings and audio tags read before each answer. A persona
    # left out, or without a voice_id, speaks with ARI_ELEVENLABS_VOICE_ID and default settings.
    elevenlabs_exam_personas: Annotated[dict[str, ExamPersonaVoice], NoDecode] = Field(
        default_factory=dict
    )
    # Open-microphone speech detection of the chain (hypotheses, to tune on real calls): RMS of
    # a 20 ms PCM16 frame that counts as speech, speech needed to start, silence that ends it.
    exam_vad_threshold: int = Field(default=500, ge=1)
    exam_vad_min_speech_ms: int = Field(default=200, ge=20)
    exam_vad_silence_ms: int = Field(default=900, ge=100)

    @field_validator("elevenlabs_exam_personas", mode="before")
    @classmethod
    def personas_from_json(cls, value: object) -> object:
        """JSON in the environment; compose passes an unset variable as an empty string."""
        if isinstance(value, str):
            return json.loads(value) if value.strip() else {}
        return value

    @field_validator("elevenlabs_exam_personas")
    @classmethod
    def personas_are_known(cls, value: dict[str, ExamPersonaVoice]) -> dict[str, ExamPersonaVoice]:
        unknown = sorted(set(value) - set(PERSONA_VARIANTS))
        if unknown:
            raise ValueError(f"unknown personas {unknown}; expected {list(PERSONA_VARIANTS)}")
        return value

    realtime_model: str = "gpt-realtime-mini"
    realtime_voice: str = "marin"
    realtime_transcription_model: str = "gpt-4o-mini-transcribe"
    realtime_vad_silence_ms: int = 900
    realtime_connect_timeout_seconds: float = 15.0
    audio_sample_rate: int = 24_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
