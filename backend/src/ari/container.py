from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from ari.application.ports.llm import LLMProvider
from ari.application.ports.stt import UtteranceTranscriber
from ari.application.ports.tts import StreamingTTSProvider
from ari.application.prompting import load_prompt
from ari.application.services.conversation import ConversationOrchestrator
from ari.application.services.evaluation import LLMBackedEvaluator
from ari.application.services.patient import PatientSimulator
from ari.application.services.practice import PracticeService
from ari.application.voice_stacks import VoiceStack
from ari.config import Settings
from ari.domain.errors import InvalidStateError
from ari.infrastructure.cases.clinical_catalog import ClinicalCatalog
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.practice_catalog import PublishedPracticeCatalog
from ari.infrastructure.cases.practice_demos import synthetic_demos
from ari.infrastructure.persistence.practice import SqlPracticeRepository
from ari.infrastructure.persistence.sqlite import SqliteSessionRepository
from ari.infrastructure.providers.fake import FakeLLMProvider, FakeTranscriber, FakeTTSProvider


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    repository: SqliteSessionRepository
    cases: ClinicalCatalog
    orchestrator: ConversationOrchestrator
    transcriber: UtteranceTranscriber
    tts: StreamingTTSProvider
    voice_stack: VoiceStack
    practice: PracticeService


def _voice_stack(settings: Settings) -> VoiceStack:
    """The single versioned voice configuration pinned on every session."""
    return VoiceStack(
        "pipeline_economy",
        "1",
        settings.provider_mode,
        MappingProxyType(
            {"stt": settings.stt_model, "llm": settings.patient_model, "tts": settings.tts_model}
        ),
        MappingProxyType(
            {
                "sample_rate": settings.audio_sample_rate,
                "stt_base_url": settings.stt_base_url,
                "tts_voice": settings.tts_voice,
            }
        ),
    )


def build_container(settings: Settings) -> Container:
    if settings.enable_mvp_demos and settings.environment == "production":
        raise InvalidStateError("Synthetic MVP demos are forbidden in production")
    if settings.database_url.startswith("sqlite:///"):
        from pathlib import Path

        Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(
            parents=True, exist_ok=True
        )
    repository = SqliteSessionRepository(settings.database_url)
    cases = ClinicalCatalog(ClinicalStore(repository.engine))
    patient_prompt = load_prompt(settings.prompt_directory / "patient_v2.txt", "patient-v2")
    evaluation_prompt = load_prompt(
        settings.prompt_directory / "evaluation_v2.txt", "evaluation-v2"
    )
    voice_stack = _voice_stack(settings)
    llm: LLMProvider
    transcriber: UtteranceTranscriber
    tts: StreamingTTSProvider
    if settings.provider_mode == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("ARI_OPENAI_API_KEY is required when ARI_PROVIDER_MODE=openai")
        if not settings.stt_api_key:
            raise RuntimeError("ARI_STT_API_KEY is required when ARI_PROVIDER_MODE=openai")
        from ari.infrastructure.providers.openai import (
            OpenAILLMProvider,
            OpenAIStreamingTTSProvider,
        )
        from ari.infrastructure.providers.whisper import WhisperTranscriber

        llm = OpenAILLMProvider(
            settings.openai_api_key,
            patient_model=settings.patient_model,
            evaluation_model=settings.evaluation_model,
            patient_timeout_seconds=settings.patient_timeout_seconds,
            evaluation_timeout_seconds=settings.evaluation_timeout_seconds,
        )
        transcriber = WhisperTranscriber(
            settings.stt_api_key,
            base_url=settings.stt_base_url,
            model=settings.stt_model,
            timeout_seconds=settings.stt_timeout_seconds,
        )
        tts = OpenAIStreamingTTSProvider(
            settings.openai_api_key,
            model=settings.tts_model,
            voice=settings.tts_voice,
            timeout_seconds=settings.tts_timeout_seconds,
        )
    else:
        llm = FakeLLMProvider()
        transcriber = FakeTranscriber()
        tts = FakeTTSProvider()

    orchestrator = ConversationOrchestrator(
        repository,
        cases,
        PatientSimulator(llm, patient_prompt),
        LLMBackedEvaluator(llm, evaluation_prompt, settings.feedback_language),
        voice_stack,
    )
    practice_service = PracticeService(
        PublishedPracticeCatalog(
            cases.store, synthetic_demos() if settings.enable_mvp_demos else (),
        ),
        SqlPracticeRepository(repository.engine, allow_synthetic=settings.enable_mvp_demos),
    )
    return Container(
        settings=settings,
        repository=repository,
        cases=cases,
        orchestrator=orchestrator,
        transcriber=transcriber,
        tts=tts,
        voice_stack=voice_stack,
        practice=practice_service,
    )
