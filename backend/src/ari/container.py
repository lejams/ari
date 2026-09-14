from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ari.application.ports.llm import LLMProvider
from ari.application.ports.realtime_voice import RealtimeVoiceEngine
from ari.application.ports.stt import StreamingSTTProvider
from ari.application.ports.tts import StreamingTTSProvider
from ari.application.prompting import load_prompt
from ari.application.services.conversation import ConversationOrchestrator
from ari.application.services.evaluation import LLMBackedEvaluator
from ari.application.services.grounding import GroundingAuditor
from ari.application.services.patient import PatientSimulator
from ari.application.services.practice import PracticeService
from ari.application.services.realtime import PatientOpeningBuilder, RealtimeSimulationBuilder
from ari.application.services.voice import TurnBasedVoiceEngine
from ari.application.voice_stacks import VoiceStack, VoiceStackRegistry, VoiceTransport
from ari.config import Settings
from ari.domain.errors import InvalidStateError
from ari.infrastructure.cases.clinical_catalog import ClinicalCatalog
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.loader import load_cases
from ari.infrastructure.cases.practice_catalog import PublishedPracticeCatalog
from ari.infrastructure.cases.practice_demos import synthetic_demos
from ari.infrastructure.learning_aids.loader import (
    YamlVocabularyHintCatalog,
    load_vocabulary_hints,
)
from ari.infrastructure.persistence.practice import SqlPracticeRepository
from ari.infrastructure.persistence.sqlite import SqliteSessionRepository
from ari.infrastructure.providers.fake import FakeLLMProvider, FakeSTTProvider, FakeTTSProvider


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    repository: SqliteSessionRepository
    cases: ClinicalCatalog
    vocabulary_hints: YamlVocabularyHintCatalog
    orchestrator: ConversationOrchestrator
    voice: TurnBasedVoiceEngine
    realtime_voice: RealtimeVoiceEngine | None
    realtime_simulation: RealtimeSimulationBuilder
    patient_opening: PatientOpeningBuilder
    grounding_auditor: GroundingAuditor
    voice_stacks: VoiceStackRegistry
    pipeline_voices: Mapping[str, TurnBasedVoiceEngine]
    realtime_voices: Mapping[str, RealtimeVoiceEngine]
    practice: PracticeService

    def voice_stack_available(self, stack: VoiceStack) -> bool:
        if stack.transport is VoiceTransport.REALTIME:
            return self.realtime_voice_for(stack.id) is not None
        try:
            self.pipeline_voice_for(stack.id)
        except InvalidStateError:
            return False
        return True

    def pipeline_voice_for(self, stack_id: str) -> TurnBasedVoiceEngine:
        if stack_id == "pipeline_economy":
            return self.voice
        try:
            return self.pipeline_voices[stack_id]
        except KeyError as exc:
            raise InvalidStateError(f"Pipeline voice stack {stack_id} is unavailable") from exc

    def realtime_voice_for(self, stack_id: str) -> RealtimeVoiceEngine | None:
        if stack_id not in {"realtime_economy", "realtime_quality"}:
            return None
        return self.realtime_voices.get(stack_id) or self.realtime_voice


def _voice_stack_registry(settings: Settings) -> VoiceStackRegistry:
    common_parameters = MappingProxyType(
        {
            "sample_rate": settings.audio_sample_rate,
            "vad_threshold": settings.vad_threshold,
            "vad_prefix_padding_ms": settings.vad_prefix_padding_ms,
            "vad_silence_duration_ms": settings.vad_silence_duration_ms,
        }
    )
    realtime_parameters = MappingProxyType(
        {**common_parameters, "voice": "marin", "reasoning_effort": "low"}
    )
    pipeline_parameters = MappingProxyType({**common_parameters, "tts_voice": settings.tts_voice})
    provider = settings.provider_mode
    return VoiceStackRegistry(
        (
            VoiceStack(
                "realtime_economy",
                "1",
                VoiceTransport.REALTIME,
                provider,
                MappingProxyType({"realtime": settings.realtime_economy_model}),
                realtime_parameters,
            ),
            VoiceStack(
                "realtime_quality",
                "1",
                VoiceTransport.REALTIME,
                provider,
                MappingProxyType({"realtime": settings.realtime_quality_model}),
                realtime_parameters,
            ),
            VoiceStack(
                "pipeline_economy",
                "1",
                VoiceTransport.PIPELINE,
                provider,
                MappingProxyType(
                    {
                        "stt": settings.pipeline_economy_stt_model,
                        "llm": settings.patient_model,
                        "tts": settings.tts_model,
                    }
                ),
                pipeline_parameters,
            ),
            VoiceStack(
                "pipeline_low_latency",
                "1",
                VoiceTransport.PIPELINE,
                provider,
                MappingProxyType(
                    {
                        "stt": settings.pipeline_low_latency_stt_model,
                        "llm": settings.patient_model,
                        "tts": settings.tts_model,
                    }
                ),
                pipeline_parameters,
            ),
        )
    )


def build_container(settings: Settings) -> Container:
    if settings.enable_mvp_demos and settings.environment == "production":
        raise InvalidStateError("Synthetic MVP demos are forbidden in production")
    settings.case_directory.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite:///"):
        from pathlib import Path

        Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(
            parents=True, exist_ok=True
        )
    repository = SqliteSessionRepository(settings.database_url)
    legacy_cases = load_cases(
        settings.case_directory,
        include_technical_test=settings.enable_english_technical_test,
    )
    cases = ClinicalCatalog(legacy_cases, ClinicalStore(
        repository.engine, frozenset(c.id for c in legacy_cases.list()),
    ))
    vocabulary_hints = load_vocabulary_hints(settings.learning_aid_directory)
    patient_prompt = load_prompt(settings.prompt_directory / "patient_v2.txt", "patient-v2")
    evaluation_prompt = load_prompt(
        settings.prompt_directory / "evaluation_v2.txt", "evaluation-v2"
    )
    realtime_prompt = load_prompt(
        settings.prompt_directory / "realtime_patient_v2.txt", "realtime-patient-v2"
    )
    realtime_transport_prompt = load_prompt(
        settings.prompt_directory / "realtime_transport_v1.txt", "realtime-transport-v1"
    )
    grounding_prompt = load_prompt(
        settings.prompt_directory / "grounding_audit_v1.txt", "grounding-audit-v1"
    )
    voice_stacks = _voice_stack_registry(settings)
    preferred_voice_transport = (
        VoiceTransport.PIPELINE
        if settings.provider_mode == "fake"
        else VoiceTransport(settings.voice_transport)
    )
    llm: LLMProvider
    stt: StreamingSTTProvider
    tts: StreamingTTSProvider
    realtime_voice: RealtimeVoiceEngine | None = None
    pipeline_voices: dict[str, TurnBasedVoiceEngine] = {}
    realtime_voices: dict[str, RealtimeVoiceEngine] = {}

    if settings.provider_mode == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("ARI_OPENAI_API_KEY is required when ARI_PROVIDER_MODE=openai")
        from ari.infrastructure.providers.openai import (
            OpenAILLMProvider,
            OpenAIRealtimeVoiceEngine,
            OpenAIStreamingSTTProvider,
            OpenAIStreamingTTSProvider,
        )

        llm = OpenAILLMProvider(
            settings.openai_api_key,
            patient_model=settings.patient_model,
            grounding_model=settings.grounding_model,
            evaluation_model=settings.evaluation_model,
            patient_timeout_seconds=settings.patient_timeout_seconds,
            evaluation_timeout_seconds=settings.evaluation_timeout_seconds,
        )
        stt = OpenAIStreamingSTTProvider(
            settings.openai_api_key,
            model=settings.pipeline_economy_stt_model,
            realtime_url=settings.openai_realtime_url,
            sample_rate=settings.audio_sample_rate,
            silence_ms=settings.vad_silence_duration_ms,
            prefix_padding_ms=settings.vad_prefix_padding_ms,
            vad_threshold=settings.vad_threshold,
            handshake_timeout_seconds=settings.stt_handshake_timeout_seconds,
        )
        tts = OpenAIStreamingTTSProvider(
            settings.openai_api_key,
            model=settings.tts_model,
            voice=settings.tts_voice,
            timeout_seconds=settings.tts_timeout_seconds,
        )
        low_latency_stt = OpenAIStreamingSTTProvider(
            settings.openai_api_key,
            model=settings.pipeline_low_latency_stt_model,
            realtime_url=settings.openai_realtime_url,
            sample_rate=settings.audio_sample_rate,
            silence_ms=settings.vad_silence_duration_ms,
            prefix_padding_ms=settings.vad_prefix_padding_ms,
            vad_threshold=settings.vad_threshold,
            handshake_timeout_seconds=settings.stt_handshake_timeout_seconds,
        )
        pipeline_voices = {
            "pipeline_economy": TurnBasedVoiceEngine(stt, tts),
            "pipeline_low_latency": TurnBasedVoiceEngine(low_latency_stt, tts),
        }
        realtime_voice = OpenAIRealtimeVoiceEngine(
            settings.openai_api_key,
            calls_url=settings.openai_realtime_calls_url,
            sideband_url=settings.openai_realtime_url,
            timeout_seconds=settings.realtime_handshake_timeout_seconds,
            quality_model=settings.realtime_quality_model,
            economy_model=settings.realtime_economy_model,
        )
        realtime_voices = {
            "realtime_economy": realtime_voice,
            "realtime_quality": realtime_voice,
        }
    else:
        llm = FakeLLMProvider()
        stt = FakeSTTProvider()
        tts = FakeTTSProvider()
        pipeline_voices = {
            "pipeline_economy": TurnBasedVoiceEngine(stt, tts),
            "pipeline_low_latency": TurnBasedVoiceEngine(stt, tts),
        }

    orchestrator = ConversationOrchestrator(
        repository,
        cases,
        PatientSimulator(llm, patient_prompt),
        LLMBackedEvaluator(llm, evaluation_prompt, settings.feedback_language),
        voice_stacks,
        preferred_voice_transport,
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
        vocabulary_hints=vocabulary_hints,
        orchestrator=orchestrator,
        voice=TurnBasedVoiceEngine(stt, tts),
        realtime_voice=realtime_voice,
        realtime_simulation=RealtimeSimulationBuilder(
            realtime_prompt, realtime_transport_prompt
        ),
        patient_opening=PatientOpeningBuilder(),
        grounding_auditor=GroundingAuditor(llm, grounding_prompt),
        voice_stacks=voice_stacks,
        pipeline_voices=MappingProxyType(pipeline_voices),
        realtime_voices=MappingProxyType(realtime_voices),
        practice=practice_service,
    )
