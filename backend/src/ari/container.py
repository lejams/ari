from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from ari.application.ports.llm import LLMProvider
from ari.application.ports.realtime import RealtimeVoiceEngine
from ari.application.ports.stt import UtteranceTranscriber
from ari.application.ports.tts import StreamingTTSProvider
from ari.application.prompting import VersionedPrompt, load_prompt
from ari.application.services.conversation import ConversationOrchestrator
from ari.application.services.evaluation import LLMBackedEvaluator
from ari.application.services.lexicon import LexiconService
from ari.application.services.patient import PatientSimulator
from ari.application.services.placement import PlacementService
from ari.application.services.practice import PracticeService
from ari.application.services.realtime_patient import FactAttributor
from ari.application.voice_stacks import VoiceStack
from ari.config import Settings
from ari.domain.errors import InvalidStateError
from ari.domain.models import ConversationSession
from ari.infrastructure.cases.clinical_catalog import ClinicalCatalog
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.placement_store import PlacementStore
from ari.infrastructure.cases.practice_catalog import PublishedPracticeCatalog
from ari.infrastructure.persistence.lexicon import SqlLexiconRepository
from ari.infrastructure.persistence.placement import SqlPlacementRepository
from ari.infrastructure.persistence.practice import SqlPracticeRepository
from ari.infrastructure.persistence.sqlite import SqliteSessionRepository
from ari.infrastructure.providers.fake import (
    FakeLLMProvider,
    FakeRealtimeEngine,
    FakeTranscriber,
    FakeTTSProvider,
)

PIPELINE_STACK_ID = "pipeline_economy"
REALTIME_STACK_ID = "realtime_exam"


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
    realtime: RealtimeVoiceEngine
    realtime_stack: VoiceStack
    realtime_prompt: VersionedPrompt
    attributor: FactAttributor
    lexicon: LexiconService
    placement: PlacementService

    def resolve_voice_stack(self, session: ConversationSession) -> VoiceStack:
        """The exact stack a session was created with, or an error: never a silent swap."""
        for stack in (self.voice_stack, self.realtime_stack):
            if stack.id == session.voice_stack_id:
                return stack.resolve_persisted(
                    session.voice_stack_id, session.voice_stack_version, session.voice_stack_config
                )
        raise InvalidStateError(f"Unknown voice stack {session.voice_stack_id}")


def _voice_stack(settings: Settings) -> VoiceStack:
    """Training: push-to-talk pipeline (Whisper, structured LLM, TTS), pinned on the session."""
    return VoiceStack(
        PIPELINE_STACK_ID,
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


def _realtime_stack(settings: Settings) -> VoiceStack:
    """Exam: open microphone, one speech-to-speech model, LLM audit of revealed facts."""
    return VoiceStack(
        REALTIME_STACK_ID,
        "1",
        settings.provider_mode,
        MappingProxyType(
            {
                "sts": settings.realtime_model,
                "stt": settings.realtime_transcription_model,
                "llm": settings.patient_model,
            }
        ),
        MappingProxyType(
            {
                "sample_rate": settings.audio_sample_rate,
                "voice": settings.realtime_voice,
                "vad_silence_ms": settings.realtime_vad_silence_ms,
            }
        ),
    )


def build_container(settings: Settings) -> Container:
    if settings.database_url.startswith("sqlite:///"):
        from pathlib import Path

        Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(
            parents=True, exist_ok=True
        )
    repository = SqliteSessionRepository(settings.database_url)
    cases = ClinicalCatalog(ClinicalStore(repository.engine))
    patient_prompt = load_prompt(settings.prompt_directory / "patient_v3.txt", "patient-v3")
    evaluation_prompt = load_prompt(
        settings.prompt_directory / "evaluation_v5.txt", "evaluation-v5"
    )
    realtime_prompt = load_prompt(
        settings.prompt_directory / "patient_realtime_v1.txt", "patient-realtime-v1"
    )
    attribution_prompt = load_prompt(
        settings.prompt_directory / "fact_attribution_v1.txt", "fact-attribution-v1"
    )
    speaking_prompt = load_prompt(
        settings.prompt_directory / "placement_speaking_v1.txt", "placement-speaking-v1"
    )
    voice_stack = _voice_stack(settings)
    realtime_stack = _realtime_stack(settings)
    llm: LLMProvider
    transcriber: UtteranceTranscriber
    tts: StreamingTTSProvider
    realtime: RealtimeVoiceEngine
    if settings.provider_mode == "openai":
        missing = [
            name
            for name, value in (
                ("ARI_OPENAI_API_KEY", settings.openai_api_key),
                ("ARI_STT_API_KEY", settings.stt_api_key),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"{', '.join(missing)} required in .env when ARI_PROVIDER_MODE=openai. "
                f"Whisper defaults to Groq ({settings.stt_base_url}); to use your OpenAI key "
                "for transcription too, set ARI_STT_BASE_URL=https://api.openai.com/v1, "
                "ARI_STT_MODEL=whisper-1 and ARI_STT_API_KEY to the same key."
            )
        assert settings.openai_api_key and settings.stt_api_key
        from ari.infrastructure.providers.openai import (
            OpenAILLMProvider,
            OpenAIRealtimeEngine,
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
        realtime = OpenAIRealtimeEngine(
            settings.openai_api_key,
            model=settings.realtime_model,
            voice=settings.realtime_voice,
            transcription_model=settings.realtime_transcription_model,
            vad_silence_ms=settings.realtime_vad_silence_ms,
            connect_timeout_seconds=settings.realtime_connect_timeout_seconds,
        )
    else:
        llm = FakeLLMProvider()
        transcriber = FakeTranscriber()
        tts = FakeTTSProvider()
        realtime = FakeRealtimeEngine()

    lexicon = LexiconService(SqlLexiconRepository(repository.engine))
    orchestrator = ConversationOrchestrator(
        repository,
        cases,
        PatientSimulator(llm, patient_prompt),
        LLMBackedEvaluator(llm, evaluation_prompt, settings.feedback_language),
        voice_stack,
        exam_voice_stack=realtime_stack,
        lexicon=lexicon,
    )
    practice_service = PracticeService(
        PublishedPracticeCatalog(cases.store), SqlPracticeRepository(repository.engine)
    )
    placement = PlacementService(
        PlacementStore(repository.engine),
        SqlPlacementRepository(repository.engine),
        repository,
        transcriber,
        tts,
        llm,
        speaking_prompt,
        feedback_language=settings.feedback_language,
        sample_rate=settings.audio_sample_rate,
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
        realtime=realtime,
        realtime_stack=realtime_stack,
        realtime_prompt=realtime_prompt,
        attributor=FactAttributor(llm, attribution_prompt),
        lexicon=lexicon,
        placement=placement,
    )
