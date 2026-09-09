"""Unified lifecycle facade over the legacy structured and voice workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, cast

from ari.application.ports.repository import SessionRepository
from ari.application.services.conversation import ConversationOrchestrator
from ari.application.services.practice import PracticeService
from ari.application.services.practice_projection import project_structured, project_voice
from ari.domain.errors import InvalidStateError
from ari.domain.models import ConversationSession, InteractionMode, VoiceProfile, new_id
from ari.domain.practice import PracticeRun
from ari.domain.practice_lifecycle import (
    PracticeFeedback,
    PracticeHandle,
    PracticeHandleKind,
    PracticeModality,
    PracticeStateProjection,
    PracticeTransport,
    PracticeTurn,
)


class StructuredTextWorkflow:
    kind = PracticeHandleKind.STRUCTURED_TEXT.value

    def __init__(self, service: PracticeService) -> None:
        self.service = service

    def start(self, learner_id: str, **parameters: Any) -> PracticeHandle:
        run = self.service.start(
            learner_id,
            str(parameters["scenario_id"]),
            str(parameters.get("scenario_version", parameters.get("version", ""))),
            parameters.get("mode", "training"),
            str(parameters["request_id"]),
        )
        return project_structured(run).handle

    def _run(self, handle: PracticeHandle) -> PracticeRun:
        return self.service.repository.get(handle.id, handle.learner_id)

    async def submit_turn(
        self, handle: PracticeHandle, text: str, **parameters: Any
    ) -> PracticeTurn:
        current = self._run(handle)
        specification = current.content.bundle.scenarios[0].practice
        if specification is None or len(current.answers) >= len(specification.questions):
            raise InvalidStateError("Toutes les questions ont déjà une réponse")
        run = self.service.answer(
            handle.learner_id,
            handle.id,
            str(parameters.get("question_id", specification.questions[len(current.answers)].id)),
            text,
            str(parameters.get("event_id", new_id())),
        )
        return project_structured(run).turns[-1]

    def resume(self, handle: PracticeHandle) -> PracticeStateProjection:
        run = self._run(handle)
        run = self.service.repository.set_paused(handle.id, handle.learner_id, False)
        return project_structured(run)

    async def end(self, handle: PracticeHandle) -> PracticeStateProjection:
        return project_structured(self.service.finish(handle.learner_id, handle.id))

    def feedback(self, handle: PracticeHandle) -> PracticeFeedback | None:
        return project_structured(self._run(handle)).feedback


class PatientVoiceWorkflow:
    kind = PracticeHandleKind.PATIENT_VOICE.value

    def __init__(
        self, orchestrator: ConversationOrchestrator, repository: SessionRepository
    ) -> None:
        self.orchestrator, self.repository = orchestrator, repository

    def start(self, learner_id: str, **parameters: Any) -> PracticeHandle:
        request_id = parameters.get("request_id")
        request_hash = parameters.get("request_hash")
        if request_id is not None and request_hash is None:
            request_hash = hashlib.sha256(
                json.dumps(parameters, sort_keys=True, default=str).encode()
            ).hexdigest()
        session = self.orchestrator.create_session(
            learner_id,
            str(parameters["case_id"]),
            str(parameters.get("case_version", parameters.get("version", ""))),
            parameters.get("voice_profile", VoiceProfile.ECONOMY),
            parameters.get("interaction_mode", InteractionMode.GUIDED),
            learning_mode=parameters.get("learning_mode"),
            start_request_id=parameters.get("request_id"),
            start_request_hash=request_hash,
            voice_stack_id=parameters.get("voice_stack_id"),
            scenario_id=parameters.get("scenario_id"),
            scenario_version=parameters.get("scenario_version"),
        )
        transport = (
            PracticeTransport.REALTIME
            if session.voice_stack_config.get("transport") == "realtime"
            else PracticeTransport.PIPELINE
        )
        return PracticeHandle(
            id=session.id,
            learner_id=session.learner_id,
            kind=PracticeHandleKind.PATIENT_VOICE,
            modality=PracticeModality.VOICE,
            transport=transport,
            status=session.status.value,
            request_id=session.start_request_id,
            case_id=session.case_id,
            case_version=session.case_version,
            created_at=session.created_at,
        )

    def _session(self, handle: PracticeHandle) -> ConversationSession:
        session = self.repository.get_session(handle.id)
        if session.learner_id != handle.learner_id:
            raise InvalidStateError("Practice handle ownership mismatch")
        return session

    async def submit_turn(
        self, handle: PracticeHandle, text: str, **parameters: Any
    ) -> PracticeTurn:
        session = self._session(handle)
        if session.status.value == "created":
            self.orchestrator.activate(session.id)
        await self.orchestrator.process_transcript(
            handle.id,
            text,
            turn_id=parameters.get("turn_id"),
            provider_input_item_id=parameters.get("provider_input_item_id"),
        )
        latest = self.repository.get_session(handle.id)
        return project_voice(latest).turns[-1]

    async def complete_reserved_turn(self, handle: PracticeHandle, turn_id: str) -> PracticeTurn:
        """Complete a transcript already reserved by a transport (pipeline/STT)."""
        session = self._session(handle)
        turn = next((item for item in session.turns if item.id == turn_id), None)
        if turn is None:
            raise InvalidStateError("Reserved practice turn not found")
        await self.orchestrator.complete_turn(turn)
        return project_voice(self.repository.get_session(handle.id)).turns[-1]

    def resume(self, handle: PracticeHandle) -> PracticeStateProjection:
        session = self._session(handle)
        if session.status.value == "created":
            self.orchestrator.activate(session.id)
            session = self.repository.get_session(handle.id)
        return project_voice(session)

    async def end(self, handle: PracticeHandle) -> PracticeStateProjection:
        outcome = await self.orchestrator.end_session(handle.id)
        return project_voice(outcome.session)

    def feedback(self, handle: PracticeHandle) -> PracticeFeedback | None:
        return project_voice(self._session(handle)).feedback


class PracticeLifecycle:
    """Registry and lifecycle operations; stores remain physically separate."""

    def __init__(
        self,
        structured: StructuredTextWorkflow | Mapping[str, Any],
        voice: PatientVoiceWorkflow | None = None,
    ) -> None:
        if isinstance(structured, Mapping):
            self.registry = dict(structured)
        else:
            if voice is None:
                raise ValueError("Both structured and voice workflows are required")
            self.registry = {
                PracticeHandleKind.STRUCTURED_TEXT.value: structured,
                PracticeHandleKind.PATIENT_VOICE.value: voice,
            }

    def workflow(self, kind: PracticeHandleKind | str) -> Any:
        key = kind.value if isinstance(kind, PracticeHandleKind) else str(kind)
        try:
            return self.registry[key]
        except KeyError as exc:
            raise InvalidStateError(f"Unknown practice kind: {key}") from exc

    def start_practice(
        self, kind: PracticeHandleKind | str, learner_id: str, **parameters: Any
    ) -> PracticeHandle:
        return cast(PracticeHandle, self.workflow(kind).start(learner_id, **parameters))

    def _workflow_for_handle(
        self, handle: PracticeHandle | str, kind: str | None = None
    ) -> tuple[Any, PracticeHandle]:
        if isinstance(handle, str):
            if kind is None:
                raise InvalidStateError("kind is required when using a handle ID")
            kind_key = kind.value if isinstance(kind, PracticeHandleKind) else kind
            assert kind_key is not None
            workflow = self.workflow(kind_key)
            # Both repositories are intentionally queried only by their own workflow.
            if kind_key == PracticeHandleKind.STRUCTURED_TEXT.value:
                run = workflow.service.repository.get_any(handle)
                h = project_structured(run).handle
            else:
                session = workflow.repository.get_session(handle)
                h = project_voice(session).handle
            return workflow, h
        return self.workflow(handle.kind), handle

    async def submit_turn(
        self,
        handle: PracticeHandle | str,
        text: str,
        *,
        kind: str | None = None,
        **parameters: Any,
    ) -> PracticeTurn:
        workflow, resolved = self._workflow_for_handle(handle, kind)
        return cast(
            PracticeTurn,
            await workflow.submit_turn(resolved, text, **parameters),
        )

    def resume_practice(
        self, handle: PracticeHandle | str, *, kind: str | None = None
    ) -> PracticeStateProjection:
        workflow, resolved = self._workflow_for_handle(handle, kind)
        return cast(PracticeStateProjection, workflow.resume(resolved))

    async def end_practice(
        self, handle: PracticeHandle | str, *, kind: str | None = None
    ) -> PracticeStateProjection:
        workflow, resolved = self._workflow_for_handle(handle, kind)
        return cast(PracticeStateProjection, await workflow.end(resolved))

    def get_feedback(
        self, handle: PracticeHandle | str, *, kind: str | None = None
    ) -> PracticeFeedback | None:
        workflow, resolved = self._workflow_for_handle(handle, kind)
        return cast(PracticeFeedback | None, workflow.feedback(resolved))

    def get_projection(
        self, handle: PracticeHandle | str, *, kind: str | None = None
    ) -> PracticeStateProjection:
        workflow, resolved = self._workflow_for_handle(handle, kind)
        if isinstance(workflow, StructuredTextWorkflow):
            return project_structured(workflow._run(resolved))
        return project_voice(workflow._session(resolved))

    def handle_for(
        self, kind: PracticeHandleKind | str, identifier: str, learner_id: str
    ) -> PracticeHandle:
        workflow = self.workflow(kind)
        if isinstance(workflow, StructuredTextWorkflow):
            run = workflow.service.repository.get(identifier, learner_id)
            return project_structured(run).handle
        return project_voice(workflow.repository.get_session(identifier)).handle

    def voice_handle(self, session_id: str) -> PracticeHandle:
        workflow = self.workflow(PracticeHandleKind.PATIENT_VOICE)
        return project_voice(workflow.repository.get_session(session_id)).handle

    async def complete_reserved_turn(self, session_id: str, turn_id: str) -> PracticeTurn:
        workflow = self.workflow(PracticeHandleKind.PATIENT_VOICE)
        return cast(
            PracticeTurn,
            await workflow.complete_reserved_turn(self.voice_handle(session_id), turn_id),
        )
