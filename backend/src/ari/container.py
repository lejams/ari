from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from ari.application.ports.email import EmailSender
from ari.application.ports.llm import LLMProvider
from ari.application.ports.realtime import RealtimeVoiceEngine
from ari.application.ports.stt import UtteranceTranscriber
from ari.application.ports.tts import StreamingTTSProvider
from ari.application.prompting import VersionedPrompt, load_prompt
from ari.application.services.accounts import LearnerAuth
from ari.application.services.conversation import ConversationOrchestrator
from ari.application.services.evaluation import LLMBackedEvaluator
from ari.application.services.exam_session import CHAIN_EXAM_STACK_ID, ExamPatient, ExamVoices
from ari.application.services.lexicon import LexiconService
from ari.application.services.patient import PatientSimulator
from ari.application.services.placement import PlacementService
from ari.application.services.practice import PracticeService
from ari.application.services.realtime_patient import FactAttributor
from ari.application.services.usage_guard import UsageGuard
from ari.application.voice_stacks import VoiceStack
from ari.config import ExamPersonaVoice, Settings
from ari.domain.clinical import PERSONA_VARIANTS
from ari.domain.errors import InvalidStateError
from ari.domain.models import ConversationSession
from ari.infrastructure.cases.clinical_catalog import ClinicalCatalog
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.placement_store import PlacementStore
from ari.infrastructure.cases.practice_catalog import PublishedPracticeCatalog
from ari.infrastructure.persistence.platform.accounts import SqlLearnerAccountStore
from ari.infrastructure.persistence.platform.lexicon import SqlLexiconRepository
from ari.infrastructure.persistence.platform.placement import SqlPlacementRepository
from ari.infrastructure.persistence.platform.practice import SqlPracticeRepository
from ari.infrastructure.persistence.platform.repository import SqlSessionRepository
from ari.infrastructure.persistence.platform.usage import SqlUsageLedger
from ari.infrastructure.pricing import load_pricing
from ari.infrastructure.providers.email import LogEmailSender, SmtpEmailSender
from ari.infrastructure.providers.fake import (
    FakeLLMProvider,
    FakeRealtimeEngine,
    FakeTranscriber,
    FakeTTSProvider,
)

logger = logging.getLogger(__name__)

PIPELINE_STACK_ID = "pipeline_economy"
REALTIME_STACK_ID = "realtime_exam"


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    repository: SqlSessionRepository
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
    email: EmailSender
    auth: LearnerAuth
    usage_guard: UsageGuard
    # The exam chain (ARI_EXAM_ENGINE=chain); None when it is off, and its sessions then refuse
    # to resume rather than continue on another engine.
    chain_stack: VoiceStack | None = None
    exam_voices: ExamVoices | None = None

    def resolve_voice_stack(self, session: ConversationSession) -> VoiceStack:
        """The exact stack a session was created with, or an error: never a silent swap."""
        stacks = (self.voice_stack, self.realtime_stack, *(s for s in (self.chain_stack,) if s))
        for stack in stacks:
            if stack.id == session.voice_stack_id:
                return stack.resolve_persisted(
                    session.voice_stack_id, session.voice_stack_version, session.voice_stack_config
                )
        raise InvalidStateError(f"Unknown voice stack {session.voice_stack_id}")


def _voice_stack(settings: Settings) -> VoiceStack:
    """Training: push-to-talk pipeline (Whisper, structured LLM, TTS), pinned on the session."""
    parameters: dict[str, object] = {
        "sample_rate": settings.audio_sample_rate,
        "stt_base_url": settings.stt_base_url,
        "tts_voice": settings.tts_voice,
    }
    tts_model = settings.tts_model
    if settings.tts_provider == "elevenlabs":
        # Only an ElevenLabs stack names its provider: OpenAI stacks keep the snapshot they
        # already had, so sessions created before this setting existed still resume.
        tts_model = settings.elevenlabs_tts_model
        parameters["tts_voice"] = settings.elevenlabs_voice_id or ""
        parameters["tts_provider"] = "elevenlabs"
        parameters["tts_output_format"] = settings.elevenlabs_output_format
    return VoiceStack(
        PIPELINE_STACK_ID,
        "1",
        settings.provider_mode,
        MappingProxyType(
            {"stt": settings.stt_model, "llm": settings.patient_model, "tts": tts_model}
        ),
        MappingProxyType(parameters),
    )


def _tts_provider(settings: Settings) -> StreamingTTSProvider:
    """The training voice for ARI_PROVIDER_MODE=openai, chosen by ARI_TTS_PROVIDER."""
    if settings.tts_provider == "elevenlabs":
        from ari.infrastructure.providers.elevenlabs import ElevenLabsStreamingTTSProvider

        assert settings.elevenlabs_api_key and settings.elevenlabs_voice_id
        return ElevenLabsStreamingTTSProvider(
            settings.elevenlabs_api_key,
            model=settings.elevenlabs_tts_model,
            voice_id=settings.elevenlabs_voice_id,
            timeout_seconds=settings.tts_timeout_seconds,
            output_format=settings.elevenlabs_output_format,
            base_url=settings.elevenlabs_base_url,
            voice_registration=settings.elevenlabs_voice_registration,
        )
    from ari.infrastructure.providers.openai import OpenAIStreamingTTSProvider

    assert settings.openai_api_key
    return OpenAIStreamingTTSProvider(
        settings.openai_api_key,
        model=settings.tts_model,
        voice=settings.tts_voice,
        timeout_seconds=settings.tts_timeout_seconds,
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


def exam_persona_voices(settings: Settings) -> dict[str, dict[str, object]]:
    """How each persona sounds: its voice (else ARI_ELEVENLABS_VOICE_ID), settings and tags."""
    fallback = settings.elevenlabs_voice_id or ""
    voices: dict[str, dict[str, object]] = {}
    for persona in PERSONA_VARIANTS:
        chosen = settings.elevenlabs_exam_personas.get(persona, ExamPersonaVoice())
        voices[persona] = {
            "voice_id": chosen.voice_id or fallback,
            "tags": chosen.tags.strip(),
            "voice_settings": chosen.voice_settings.model_dump(exclude_none=True),
        }
    return voices


def _chain_stack(settings: Settings) -> VoiceStack:
    """Exam on the controlled chain: open microphone, Whisper, fact selection, ElevenLabs."""
    return VoiceStack(
        CHAIN_EXAM_STACK_ID,
        "1",
        settings.provider_mode,
        MappingProxyType(
            {
                "stt": settings.stt_model,
                "llm": settings.patient_model,
                "tts": settings.elevenlabs_exam_tts_model,
            }
        ),
        MappingProxyType(
            {
                "sample_rate": settings.audio_sample_rate,
                "stt_base_url": settings.stt_base_url,
                "tts_provider": "elevenlabs",
                "tts_output_format": settings.elevenlabs_output_format,
                "personas": exam_persona_voices(settings),
                "vad_threshold": settings.exam_vad_threshold,
                "vad_min_speech_ms": settings.exam_vad_min_speech_ms,
                "vad_silence_ms": settings.exam_vad_silence_ms,
            }
        ),
    )


def _exam_voices(settings: Settings) -> ExamVoices:
    """One ElevenLabs provider (one connection pool) per distinct way of speaking."""
    from ari.infrastructure.providers.elevenlabs import ElevenLabsStreamingTTSProvider

    assert settings.elevenlabs_api_key
    by_sound: dict[str, StreamingTTSProvider] = {}
    providers: dict[str, StreamingTTSProvider] = {}
    for persona, sound in exam_persona_voices(settings).items():
        key = json.dumps(sound, sort_keys=True)
        if key not in by_sound:
            by_sound[key] = ElevenLabsStreamingTTSProvider(
                settings.elevenlabs_api_key,
                model=settings.elevenlabs_exam_tts_model,
                voice_id=str(sound["voice_id"]),
                timeout_seconds=settings.tts_timeout_seconds,
                output_format=settings.elevenlabs_output_format,
                base_url=settings.elevenlabs_base_url,
                voice_registration=settings.elevenlabs_voice_registration,
                voice_settings=cast(dict[str, float], sound["voice_settings"]),
                audio_tags=str(sound["tags"]),
            )
        providers[persona] = by_sound[key]
    return ExamVoices(MappingProxyType(providers))


def _email_sender(settings: Settings) -> EmailSender:
    if settings.email_mode == "log":
        if settings.environment == "production":
            logger.warning("ARI_EMAIL_MODE=log in production: account links only reach the log")
        return LogEmailSender()
    if not settings.smtp_host:
        raise RuntimeError("ARI_SMTP_HOST required in .env when ARI_EMAIL_MODE=smtp.")
    return SmtpEmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        sender=settings.email_from,
        username=settings.smtp_username,
        password=settings.smtp_password,
        security=settings.smtp_security,
        timeout_seconds=settings.smtp_timeout_seconds,
    )


def build_container(settings: Settings) -> Container:
    pricing = load_pricing(settings.pricing_path)
    repository = SqlSessionRepository(settings.database_url, pricing=pricing)
    if settings.environment == "production" and "localhost" in settings.public_url:
        logger.warning("ARI_PUBLIC_URL points at localhost: e-mailed links will not work")
    email = _email_sender(settings)
    auth = LearnerAuth(
        SqlLearnerAccountStore(repository.engine),
        public_url=settings.public_url,
        session_days=settings.learner_session_days,
        activation_hours=settings.learner_activation_hours,
        reset_minutes=settings.learner_reset_minutes,
    )
    cases = ClinicalCatalog(ClinicalStore(repository.engine))
    patient_prompt = load_prompt(settings.prompt_directory / "patient_v3.txt", "patient-v3")
    evaluation_prompt = load_prompt(
        settings.prompt_directory / "evaluation_v8.txt", "evaluation-v8"
    )
    realtime_prompt = load_prompt(
        settings.prompt_directory / "patient_realtime_v1.txt", "patient-realtime-v1"
    )
    exam_prompt = load_prompt(settings.prompt_directory / "patient_exam_v1.txt", "patient-exam-v1")
    chain = settings.exam_engine == "chain"
    attribution_prompt = load_prompt(
        settings.prompt_directory / "fact_attribution_v1.txt", "fact-attribution-v1"
    )
    speaking_prompt = load_prompt(
        settings.prompt_directory / "placement_speaking_v1.txt", "placement-speaking-v1"
    )
    voice_stack = _voice_stack(settings)
    realtime_stack = _realtime_stack(settings)
    chain_stack = _chain_stack(settings) if chain else None
    exam_voices: ExamVoices | None = None
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
                *(
                    (
                        ("ARI_ELEVENLABS_API_KEY", settings.elevenlabs_api_key),
                        ("ARI_ELEVENLABS_VOICE_ID", settings.elevenlabs_voice_id),
                    )
                    if settings.tts_provider == "elevenlabs"
                    else ()
                ),
                *(
                    (
                        ("ARI_ELEVENLABS_API_KEY", settings.elevenlabs_api_key),
                        (
                            "ARI_ELEVENLABS_VOICE_ID or a voice_id per persona",
                            all(v["voice_id"] for v in exam_persona_voices(settings).values()),
                        ),
                    )
                    if chain
                    else ()
                ),
            )
            if not value
        ]
        missing = list(dict.fromkeys(missing))
        if missing:
            raise RuntimeError(
                f"{', '.join(missing)} required in .env when ARI_PROVIDER_MODE=openai. "
                f"Whisper defaults to Groq ({settings.stt_base_url}); to use your OpenAI key "
                "for transcription too, set ARI_STT_BASE_URL=https://api.openai.com/v1, "
                "ARI_STT_MODEL=whisper-1 and ARI_STT_API_KEY to the same key."
            )
        assert settings.openai_api_key and settings.stt_api_key
        from ari.infrastructure.providers.llm_routing import (
            LEARNER_TASKS,
            llm_provider_from_settings,
        )
        from ari.infrastructure.providers.openai import OpenAIRealtimeEngine
        from ari.infrastructure.providers.whisper import WhisperTranscriber

        llm = llm_provider_from_settings(settings, pricing, LEARNER_TASKS)
        transcriber = WhisperTranscriber(
            settings.stt_api_key,
            base_url=settings.stt_base_url,
            model=settings.stt_model,
            timeout_seconds=settings.stt_timeout_seconds,
        )
        tts = _tts_provider(settings)
        if chain:
            exam_voices = _exam_voices(settings)
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
        if chain:
            exam_voices = ExamVoices(MappingProxyType({"standard": tts}))

    lexicon = LexiconService(SqlLexiconRepository(repository.engine))
    orchestrator = ConversationOrchestrator(
        repository,
        cases,
        PatientSimulator(llm, patient_prompt),
        LLMBackedEvaluator(llm, evaluation_prompt, settings.feedback_language),
        voice_stack,
        exam_voice_stack=chain_stack or realtime_stack,
        lexicon=lexicon,
        exam_patient=ExamPatient(llm, exam_prompt) if chain else None,
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
        email=email,
        auth=auth,
        usage_guard=UsageGuard(
            SqlUsageLedger(repository.engine),
            exam_sessions_per_month=settings.exam_sessions_per_month,
            cost_ceiling_eur=settings.learner_cost_ceiling_eur,
        ),
        chain_stack=chain_stack,
        exam_voices=exam_voices,
    )
