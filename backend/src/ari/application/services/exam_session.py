"""Exam mode on a controlled chain: open microphone, STT, patient fact selection, streamed TTS.

The learner speaks freely; `SpeechSegmenter` cuts the microphone stream into utterances.
`ExamPatient` answers each one with the training guarantee: clinical answers are the
authored case phrases chosen by the model's source references, rendered by the code. Only
three non-clinical turn kinds (small talk, clarification request, hesitation) may use the
model's own words, and those words are screened by `screen_free_text` before they are spoken.
The persona (standard, anxious, talkative, terse) shapes the voice and that wording, never the
facts. A fact counts as heard only once the browser confirms its audio was played, exactly as
in training. `exam_report` summarises a finished exam from its persisted turns and calls.
"""

from __future__ import annotations

import math
import re
import sys
from array import array
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

from ari.application.ports.llm import LLMProvider
from ari.application.ports.tts import StreamingTTSProvider
from ari.application.prompting import VersionedPrompt
from ari.application.schemas import NON_CLINICAL_KINDS, ExamPatientResponseSchema
from ari.application.services.patient import (
    PatientOutcome,
    guard_source_refs,
    patient_request,
    render_outcome,
)
from ari.domain.clinical import PERSONA_INSTRUCTIONS
from ari.domain.models import (
    AudioDeliveryStatus,
    ConversationSession,
    ExecutionRecord,
    ExecutionStatus,
    MedicalCase,
    TurnResponseState,
)

CHAIN_EXAM_STACK_ID = "chain_exam"
DEFAULT_PERSONA = "standard"

# Spoken instead of a non-clinical reply that failed the screen: authored, fact-free wording.
NON_CLINICAL_FALLBACKS: dict[str, dict[str, str]] = {
    "de": {
        "small_talk": "Ja, danke.",
        "clarification_request": "Entschuldigung, können Sie das bitte wiederholen?",
        "hesitation": "Ja, ich warte.",
    },
    "fr": {
        "small_talk": "Oui, merci.",
        "clarification_request": "Pardon, vous pouvez répéter, s'il vous plaît ?",
        "hesitation": "D'accord, j'attends.",
    },
}

# Free wording carries no clinical content: no number, unit, duration or medical word at all.
NUMBER_WORDS = frozenset(
    {
        # German
        "null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun",
        "zehn", "elf", "zwölf", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig",
        "siebzig", "achtzig", "neunzig", "hundert", "tausend", "halb", "halbe", "halben",
        "viertel", "einmal", "zweimal", "dreimal", "erste", "ersten", "zweite", "dritte",
        # French
        "zéro", "deux", "trois", "quatre", "cinq", "sept", "huit", "neuf", "onze",
        "douze", "vingt", "trente", "quarante", "cinquante", "soixante", "cent", "mille",
        "demi", "demie", "quart", "premier", "première",
    }
)  # fmt: skip
UNIT_WORDS = frozenset(
    {
        # German
        "mg", "milligramm", "gramm", "kg", "kilo", "ml", "milliliter", "liter", "grad",
        "prozent", "mmhg", "uhr", "sekunde", "sekunden", "minute", "minuten", "stunde",
        "stunden", "tag", "tage", "tagen", "woche", "wochen", "monat", "monate", "monaten",
        "jahr", "jahre", "jahren", "tablette", "tabletten", "cm", "meter", "gestern",
        "vorgestern", "morgens", "abends", "nachts", "seit",
        # French
        "jour", "jours", "semaine", "semaines", "mois", "an", "ans", "année", "années",
        "heure", "heures", "degré", "degrés", "comprimé", "comprimés", "depuis", "hier",
    }
)  # fmt: skip
# Stems of medical vocabulary, matched inside words ("Kopfschmerzen" holds "schmerz").
MEDICAL_STEMS = (
    # German
    "schmerz", "fieber", "husten", "übel", "erbrech", "schwindel", "blut", "herz", "brust",
    "bauch", "kopf", "magen", "lunge", "atem", "luftnot", "medikament", "tablett", "allerg",
    "operation", "krankenhaus", "klinik", "diagnos", "krebs", "tumor", "druck", "zucker",
    "insulin", "infekt", "entzünd", "durchfall", "verstopf", "urin", "stuhl", "rücken",
    "bein", "arm", "hals", "auge", "ohr", "haut", "niere", "leber", "darm", "gelenk",
    "rauch", "alkohol", "schwanger", "impf", "krank", "symptom", "beschwerd",
    # French
    "douleur", "fièvre", "toux", "nausée", "vomi", "vertige", "sang", "cœur", "coeur",
    "poitrine", "ventre", "tête", "estomac", "poumon", "souffle", "médicament", "allergi",
    "opération", "hôpital", "malad", "cancer", "tension", "sucre", "diarrhée", "dos",
    "jambe", "gorge", "peau", "rein", "foie", "fumer", "tabac", "enceinte", "vaccin",
)  # fmt: skip
# Function words shared by authored phrases and harmless replies; never part of a case lexicon.
COMMON_WORDS = frozenset(
    {
        "nicht", "keine", "keinen", "einen", "einem", "einer", "eines", "meine", "meinen",
        "meiner", "meinem", "meines", "haben", "hatte", "hatten", "schon", "etwas", "immer",
        "wieder", "bitte", "danke", "gerne", "wirklich", "manchmal", "eigentlich", "glaube",
        "können", "könnte", "würde", "werden", "wurde", "jetzt", "dieser", "diese", "diesem",
        "diesen", "ihnen", "unsere", "aber", "auch", "noch", "oder", "sehr",
        "c'est", "votre", "notre", "suis", "avez", "avoir", "faire", "merci", "parce",
        "comme", "toujours", "encore", "vraiment", "peut-être", "quelque",
    }
)  # fmt: skip
WORD = re.compile(r"[^\W\d_]+(?:['\u2019-][^\W\d_]+)*", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [token.casefold() for token in WORD.findall(text)]


def case_lexicon(case: MedicalCase) -> frozenset[str]:
    """Content words of the case's facts and terminology: what free wording must not say."""
    texts = [term.german for term in case.terminology]
    for fact in case.facts:
        texts.extend((fact.value, fact.patient_phrase, *fact.patient_phrase_variants))
    return frozenset(
        token
        for text in texts
        for token in _tokens(text)
        if len(token) >= 5 and token not in COMMON_WORDS
    )


def screen_free_text(text: str, case: MedicalCase) -> str | None:
    """Why a non-clinical reply may not be spoken, or None when it may."""
    if re.search(r"\d", text):
        return "digit"
    lexicon = case_lexicon(case)
    for token in _tokens(text):
        if token in NUMBER_WORDS:
            return "number_word"
        if token in UNIT_WORDS:
            return "unit"
        if any(stem in token for stem in MEDICAL_STEMS):
            return "medical_term"
        long_word = len(token) >= 5 and token not in COMMON_WORDS
        if long_word and any(token in term or term in token for term in lexicon):
            return "case_term"
    return None


def non_clinical_fallback(language: str, kind: str) -> str:
    primary = language.split("-")[0].lower()
    return NON_CLINICAL_FALLBACKS.get(primary, NON_CLINICAL_FALLBACKS["de"])[kind]


class ExamPatient:
    """The exam patient: the training fact selection, a persona, three non-clinical kinds."""

    def __init__(self, provider: LLMProvider, prompt: VersionedPrompt) -> None:
        self._provider = provider
        self._prompt = prompt

    async def respond(
        self,
        *,
        session: ConversationSession,
        case: MedicalCase,
        user_text: str,
        turn_id: str,
    ) -> PatientOutcome:
        persona = persona_of(case)
        request = patient_request(
            self._prompt,
            session,
            case,
            user_text,
            turn_id,
            extra={
                "patient_persona": {
                    "variant": persona,
                    "speaking_style": PERSONA_INSTRUCTIONS[persona],
                }
            },
        )
        result = await self._provider.generate_structured(request, ExamPatientResponseSchema)
        value = result.value
        guard_source_refs(case, value, result.execution)
        if value.response_kind not in NON_CLINICAL_KINDS:
            return render_outcome(case, value, result.execution)
        wording = " ".join((value.free_text or "").split())
        rejected = screen_free_text(wording, case)
        # Traceability: whether the patient's own words were spoken, and why not.
        execution = replace(
            result.execution,
            usage={
                **result.execution.usage,
                "free_text": "rejected" if rejected else "accepted",
                **({"free_text_rejection": rejected} if rejected else {}),
            },
        )
        return PatientOutcome(
            spoken_text=(
                non_clinical_fallback(case.language, value.response_kind) if rejected else wording
            ),
            selected_fact_ids=(),
            execution=execution,
            response_kind=value.response_kind,
        )


def persona_of(case: MedicalCase) -> str:
    return case.persona_variant if case.persona_variant in PERSONA_INSTRUCTIONS else DEFAULT_PERSONA


@dataclass(frozen=True, slots=True)
class ExamVoices:
    """One speech provider per persona; a persona without its own voice speaks as standard."""

    providers: Mapping[str, StreamingTTSProvider]

    def for_case(self, case: MedicalCase) -> StreamingTTSProvider:
        return self.providers.get(persona_of(case)) or self.providers[DEFAULT_PERSONA]


@dataclass(frozen=True, slots=True)
class SegmentEvent:
    kind: Literal["speech_started", "utterance"]
    audio: bytes = b""


@dataclass(slots=True)
class SpeechSegmenter:
    """Energy voice activity detection on PCM16 mono: speech start, then utterance on silence.

    A frame is voiced when its RMS reaches `threshold`. Speech starts after `min_speech_ms`
    of consecutive voiced frames; the utterance (with `pre_roll_ms` of audio before the start)
    ends after `silence_ms` without a voiced frame, or at `max_utterance_seconds`.
    """

    sample_rate: int
    threshold: int
    silence_ms: int
    min_speech_ms: int
    pre_roll_ms: int = 300
    max_utterance_seconds: int = 60
    frame_ms: int = 20
    _remainder: bytes = b""
    _speaking: bool = False
    _voiced_run_ms: int = 0
    _silent_ms: int = 0
    _utterance: bytearray = field(default_factory=bytearray)
    _recent: deque[bytes] = field(default_factory=deque)

    @property
    def speaking(self) -> bool:
        return self._speaking

    def feed(self, pcm: bytes) -> list[SegmentEvent]:
        frame_bytes = self.sample_rate * self.frame_ms // 1000 * 2
        data = self._remainder + pcm
        whole = len(data) - len(data) % frame_bytes
        self._remainder = data[whole:]
        events: list[SegmentEvent] = []
        for offset in range(0, whole, frame_bytes):
            event = self._frame(data[offset : offset + frame_bytes])
            if event is not None:
                events.append(event)
        return events

    def _frame(self, frame: bytes) -> SegmentEvent | None:
        voiced = rms(frame) >= self.threshold
        if not self._speaking:
            self._recent.append(frame)
            keep = (self.pre_roll_ms + self.min_speech_ms) // self.frame_ms
            while len(self._recent) > keep:
                self._recent.popleft()
            self._voiced_run_ms = self._voiced_run_ms + self.frame_ms if voiced else 0
            if self._voiced_run_ms < self.min_speech_ms:
                return None
            self._speaking, self._silent_ms = True, 0
            self._utterance = bytearray(b"".join(self._recent))
            self._recent.clear()
            return SegmentEvent("speech_started")
        self._utterance.extend(frame)
        self._silent_ms = 0 if voiced else self._silent_ms + self.frame_ms
        too_long = len(self._utterance) >= self.sample_rate * 2 * self.max_utterance_seconds
        if self._silent_ms < self.silence_ms and not too_long:
            return None
        audio = bytes(self._utterance)
        self._speaking, self._voiced_run_ms, self._utterance = False, 0, bytearray()
        return SegmentEvent("utterance", audio)


def rms(frame: bytes) -> float:
    samples = array("h")
    samples.frombytes(frame[: len(frame) - len(frame) % 2])
    if sys.byteorder == "big":
        samples.byteswap()  # the browser sends little-endian PCM16
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def _percentile(values: list[int], share: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def exam_report(session: ConversationSession) -> dict[str, object]:
    """What happened in an exam, turn by turn, from the persisted record only.

    `response_ms` is the server's part of end of speech to first audio: transcription,
    patient selection and time to the first synthesised audio. It leaves out the silence
    that ends an utterance and the browser's playback start, and is None when a call is
    missing or failed (an unknown stays visible, never zero).
    """
    by_turn: dict[str, dict[str, ExecutionRecord]] = {}
    for execution in session.executions:
        if execution.turn_id is None or execution.status is not ExecutionStatus.SUCCEEDED:
            continue
        operations = by_turn.setdefault(execution.turn_id, {})
        operations[execution.operation] = execution
    turns: list[dict[str, object]] = []
    response_times: list[int] = []
    heard_facts: set[str] = set()
    unheard_facts: set[str] = set()
    for turn in session.turns:
        heard = turn.delivery_status is AudioDeliveryStatus.DELIVERED
        heard_facts.update(turn.revealed_fact_ids)
        if not heard:
            unheard_facts.update(turn.selected_fact_ids)
        calls = by_turn.get(turn.id, {})
        response_ms = _response_ms(calls)
        if response_ms is not None:
            response_times.append(response_ms)
        patient_call = calls.get("patient_simulation")
        turns.append(
            {
                "sequence": turn.sequence,
                "response_kind": turn.patient_response_kind,
                "answered": bool(turn.patient_text),
                "heard": heard,
                "audio_failed": turn.response_state is TurnResponseState.TTS_FAILED,
                "facts_selected": len(turn.selected_fact_ids),
                "facts_heard": len(turn.revealed_fact_ids),
                "free_text": patient_call.usage.get("free_text") if patient_call else None,
                "response_ms": response_ms,
            }
        )
    duration_seconds = (
        int((session.ended_at - session.call_started_at).total_seconds())
        if session.ended_at is not None and session.call_started_at is not None
        else None
    )
    return {
        "schema_version": "exam-report-v1",
        "voice_stack_id": session.voice_stack_id,
        "duration_seconds": duration_seconds,
        "turns": turns,
        "turn_count": len(turns),
        "heard_turns": sum(1 for item in turns if item["heard"]),
        "not_heard_turns": sum(1 for item in turns if item["answered"] and not item["heard"]),
        "audio_failures": sum(1 for item in turns if item["audio_failed"]),
        "free_text_rejections": sum(1 for item in turns if item["free_text"] == "rejected"),
        "transcription_failures": sum(
            1
            for execution in session.executions
            if execution.operation == "speech_to_text"
            and execution.status is ExecutionStatus.FAILED
        ),
        "facts_heard": len(heard_facts),
        # Selected for an answer the learner did not hear (interrupted, failed or unconfirmed).
        "facts_not_heard": len(unheard_facts - heard_facts),
        "response_ms_p50": _percentile(response_times, 0.5),
        "response_ms_p95": _percentile(response_times, 0.95),
    }


def _response_ms(calls: Mapping[str, ExecutionRecord]) -> int | None:
    stt, patient, tts = (
        calls.get(name) for name in ("speech_to_text", "patient_simulation", "text_to_speech")
    )
    if stt is None or patient is None or tts is None:
        return None
    first_audio = tts.usage.get("first_audio_ms")
    if not isinstance(first_audio, int):
        return None
    return stt.latency_ms + patient.latency_ms + first_audio
