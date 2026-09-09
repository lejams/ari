from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from hashlib import sha256

from ari.application.ports.realtime_voice import RealtimeProviderEvent, RealtimeSimulationSpec
from ari.application.prompting import VersionedPrompt
from ari.domain.models import DisclosureRule, ExecutionRecord, MedicalCase


class PatientOpeningBuilder:
    """Build the fixed first utterance from versioned case data only."""

    def build(self, case: MedicalCase) -> str:
        if case.schema_version == "clinical-case-v2":
            return case.opening_statement
        name = str(case.demographics.get("name", "")).strip()
        spontaneous = next(
            (
                fact.patient_phrase.strip()
                for fact in case.facts
                if fact.disclosure is DisclosureRule.SPONTANEOUS
            ),
            case.opening_statement.strip(),
        )
        if case.language.casefold().startswith("de"):
            introduction = f"Guten Morgen. Ich heiße {name}." if name else "Guten Morgen."
        else:
            introduction = f"Good morning. My name is {name}." if name else "Good morning."
        return f"{introduction} {spontaneous}".strip()


def spoken_word_count(text: str) -> int:
    return len(text.split())


class RealtimeSimulationBuilder:
    def __init__(
        self, prompt: VersionedPrompt, transport_prompt: VersionedPrompt | None = None
    ) -> None:
        self._prompt = prompt
        fallback_content = (
            "You are an audio transport. The application is the sole source of truth; "
            "speak only the exact response text supplied by the application. "
            "Do not simulate a patient, infer or reveal clinical facts/demographics, "
            "apply disclosure rules, or update case time."
        )
        self._transport_prompt = transport_prompt or VersionedPrompt(
            version="realtime-transport-v1",
            content=fallback_content,
            content_hash=sha256(fallback_content.encode()).hexdigest(),
        )

    def build(
        self, case: MedicalCase, *, include_timed_facts: bool = False
    ) -> RealtimeSimulationSpec:
        demographics = "\n".join(
            f"- demographics.{key}: {value}" for key, value in case.demographics.items()
        )
        available_facts = (
            case.facts
            if include_timed_facts
            else tuple(
                fact
                for fact in case.facts
                if fact.disclosure is not DisclosureRule.AFTER_5_TO_8_MINUTES_OR_NEXT_STEPS
            )
        )
        facts = "\n".join(
            f"- {fact.id} [{fact.category}; disclosure={fact.disclosure.value}]: {fact.value}"
            for fact in available_facts
        )
        instructions = self._prompt.content.format(
            language=case.language,
            title=case.title,
            opening_statement=case.opening_statement,
            communication_style=case.communication_style,
            demographics=demographics,
            facts=facts,
        )
        return RealtimeSimulationSpec(
            instructions=instructions,
            language=case.language,
            transcription_context=case.transcription_context,
            prompt_version=self._prompt.version,
            prompt_hash=sha256(instructions.encode()).hexdigest(),
        )

    def build_transport(self, case: MedicalCase) -> RealtimeSimulationSpec:
        """Return transport-only instructions for the unified Realtime path.

        The case argument is used solely for locale/context compatibility; no
        patient facts, demographics or disclosure rules cross the provider port.
        """
        instructions = self._transport_prompt.content
        return RealtimeSimulationSpec(
            instructions=instructions,
            language=case.language,
            transcription_context=case.transcription_context,
            prompt_version=self._transport_prompt.version,
            prompt_hash=sha256(instructions.encode()).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class CompletedRealtimeTurn:
    user_text: str
    patient_text: str
    input_item_id: str | None
    response_id: str
    interrupted: bool
    interruption_audio_end_ms: int | None
    response_status: str
    usage: dict[str, object]
    execution: ExecutionRecord | None


class RealtimeTurnAssembler:
    """Provider-neutral, idempotent assembly of delayed Realtime events."""

    def __init__(self) -> None:
        self._input_deltas: dict[str, str] = {}
        self._pending_inputs: deque[tuple[str | None, str]] = deque()
        self._seen_inputs: set[str] = set()
        self._response_text: dict[str, str] = {}
        self._active_response_id: str | None = None
        self._interrupted: set[str] = set()
        self._audio_end_ms: dict[str, int] = {}
        self._received_responses: set[str] = set()
        self._ready_responses: deque[
            tuple[str, str | None, dict[str, object], ExecutionRecord | None]
        ] = deque()

    def handle(self, event: RealtimeProviderEvent) -> CompletedRealtimeTurn | None:
        if event.type == "user_transcript_delta" and event.input_item_id:
            self._input_deltas[event.input_item_id] = self._input_deltas.get(
                event.input_item_id, ""
            ) + (event.text or "")
        elif event.type == "user_transcript_final":
            item_id = event.input_item_id
            if item_id is not None and item_id in self._seen_inputs:
                return self._assemble_ready()
            text = (event.text or self._input_deltas.get(item_id or "", "")).strip()
            if text:
                self._pending_inputs.append((item_id, text))
                if item_id is not None:
                    self._seen_inputs.add(item_id)
        elif event.type == "patient_speech_started" and event.response_id:
            self._active_response_id = event.response_id
        elif event.type == "patient_transcript_delta" and event.response_id:
            self._response_text[event.response_id] = self._response_text.get(
                event.response_id, ""
            ) + (event.text or "")
        elif event.type == "patient_transcript_final" and event.response_id:
            if event.text:
                self._response_text[event.response_id] = event.text
        elif event.type == "user_speech_started" and self._active_response_id:
            self._interrupted.add(self._active_response_id)
        elif event.type == "patient_interrupted" and event.response_id:
            self._interrupted.add(event.response_id)
            if event.audio_end_ms is not None:
                self._audio_end_ms[event.response_id] = event.audio_end_ms
        elif event.type == "response_completed" and event.response_id:
            response_id = event.response_id
            if response_id not in self._received_responses:
                self._received_responses.add(response_id)
                self._ready_responses.append(
                    (
                        response_id,
                        event.response_status,
                        dict(event.usage or {}),
                        event.execution,
                    )
                )
            if self._active_response_id == response_id:
                self._active_response_id = None
        return self._assemble_ready()

    def drain_ready(self, *, force_incomplete: bool = False) -> tuple[CompletedRealtimeTurn, ...]:
        completed: list[CompletedRealtimeTurn] = []
        while item := self._assemble_ready(
            force_incomplete=force_incomplete or len(self._ready_responses) > 1
        ):
            completed.append(item)
        return tuple(completed)

    def _assemble_ready(self, *, force_incomplete: bool = False) -> CompletedRealtimeTurn | None:
        if not self._ready_responses or not self._pending_inputs:
            return None
        response_id, provider_status, usage, execution = self._ready_responses[0]
        patient_text = self._response_text.get(response_id, "").strip()
        response_status = self._response_status(provider_status, execution)
        if not patient_text and response_status == "completed" and not force_incomplete:
            return None
        if not patient_text and response_status == "completed":
            response_status = "missing_transcript"
        self._ready_responses.popleft()
        input_item_id, user_text = self._pending_inputs.popleft()
        return CompletedRealtimeTurn(
            user_text=user_text,
            patient_text=patient_text,
            input_item_id=input_item_id,
            response_id=response_id,
            interrupted=response_id in self._interrupted,
            interruption_audio_end_ms=self._audio_end_ms.get(response_id),
            response_status=response_status,
            usage=usage,
            execution=execution,
        )

    @staticmethod
    def _response_status(provider_status: str | None, execution: ExecutionRecord | None) -> str:
        if provider_status and provider_status not in {"completed", "succeeded"}:
            return provider_status
        if execution is not None and execution.status.value != "succeeded":
            return (execution.error_code or "failed").removeprefix("realtime_response_")
        return "completed"


def should_release_timed_facts(user_text: str) -> bool:
    normalized = user_text.casefold()
    next_step_terms = (
        "next step",
        "what happens next",
        "our plan",
        "further tests",
        "hospital",
        "treatment",
        "nächste schritt",
        "wie geht es weiter",
        "weiter untersuch",
        "krankenhaus",
        "behandlung",
    )
    return any(term in normalized for term in next_step_terms)
