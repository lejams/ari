from dataclasses import dataclass, replace
from typing import cast

from ari.application.ports.cases import MedicalCaseCatalog
from ari.application.ports.evaluator import EvaluationOutcome, Evaluator
from ari.application.ports.repository import SessionRepository
from ari.application.services.lexicon import LexiconService
from ari.application.services.patient import PatientOutcome, PatientSimulator
from ari.application.voice_stacks import VoiceStack
from ari.domain.errors import InvalidStateError, ProviderError
from ari.domain.models import (
    ConversationSession,
    ConversationTurn,
    ExecutionRecord,
    LearnerDetails,
    LearnerProfile,
    LearningGoal,
    LearningMode,
    MedicalCase,
    SessionMetrics,
    SessionStatus,
    TurnResponseState,
    VocabularyObservation,
    VocabularyState,
    new_id,
)


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    turn: ConversationTurn
    execution: ExecutionRecord


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    session: ConversationSession


class ConversationOrchestrator:
    def __init__(
        self,
        repository: SessionRepository,
        cases: MedicalCaseCatalog,
        patient: PatientSimulator,
        evaluator: Evaluator,
        voice_stack: VoiceStack,
        exam_voice_stack: VoiceStack | None = None,
        lexicon: LexiconService | None = None,
    ) -> None:
        self.repository = repository
        self.cases = cases
        self._patient = patient
        self._evaluator = evaluator
        self._voice_stack = voice_stack
        # Exam sessions run on the open-microphone speech-to-speech stack when configured.
        self._exam_voice_stack = exam_voice_stack or voice_stack
        # Without a lexicon service the analysis completes; nothing carries across sessions.
        self._lexicon = lexicon

    def create_learner(
        self, target_cefr: object, details: LearnerDetails | None = None
    ) -> LearnerProfile:
        from ari.domain.models import CEFRLevel

        level = target_cefr if isinstance(target_cefr, CEFRLevel) else CEFRLevel(str(target_cefr))
        return self.repository.create_learner(
            LearnerProfile(
                id=new_id(),
                goal=LearningGoal(target_cefr=level),
                details=details or LearnerDetails(),
            )
        )

    def update_goal(self, learner_id: str, goal: LearningGoal) -> LearnerProfile:
        current = self.repository.get_learner(learner_id)
        return self.repository.update_learner(replace(current, goal=goal))

    def update_details(self, learner_id: str, details: LearnerDetails) -> LearnerProfile:
        current = self.repository.get_learner(learner_id)
        return self.repository.update_learner(replace(current, details=details))

    def create_session(
        self,
        learner_id: str,
        case_id: str,
        case_version: str,
        *,
        scenario_id: str | None = None,
        scenario_version: str | None = None,
        learning_mode: LearningMode | None = None,
        start_request_id: str | None = None,
        start_request_hash: str | None = None,
    ) -> ConversationSession:
        learner = self.repository.get_learner(learner_id)
        case = self.cases.get(
            case_id, case_version, scenario_id=scenario_id, scenario_version=scenario_version
        )
        if not case.available_for_new_sessions:
            raise InvalidStateError("This case has been withdrawn from new sessions")
        stack = self._exam_voice_stack if learning_mode is LearningMode.EXAM else self._voice_stack
        session = ConversationSession(
            id=new_id(),
            learner_id=learner.id,
            case_id=case.id,
            case_version=case.version,
            case_hash=case.content_hash,
            training_snapshot=dict(case.training_snapshot),
            learning_mode=learning_mode,
            start_request_id=start_request_id,
            start_request_hash=start_request_hash,
            goal=learner.goal,
            voice_stack_id=stack.id,
            voice_stack_version=stack.version,
            voice_stack_config=stack.snapshot(),
            status=SessionStatus.CREATED,
        )
        return self.repository.create_session(session)

    def activate(self, session_id: str) -> ConversationSession:
        session = self.repository.get_session(session_id)
        if session.status not in {SessionStatus.CREATED, SessionStatus.ACTIVE}:
            raise InvalidStateError(f"Cannot activate a {session.status} session")
        self._case_for_session(session)
        self.repository.set_status(session_id, SessionStatus.ACTIVE)
        self.repository.mark_call_started(session_id)
        return self.repository.get_session(session_id)

    async def process_transcript(
        self,
        session_id: str,
        text: str,
        *,
        turn_id: str | None = None,
        provider_input_item_id: str | None = None,
    ) -> TurnOutcome:
        turn = self.reserve_transcript(
            session_id,
            text,
            turn_id=turn_id,
            provider_input_item_id=provider_input_item_id,
        )
        return await self.complete_turn(turn)

    def reserve_transcript(
        self,
        session_id: str,
        text: str,
        *,
        turn_id: str | None = None,
        provider_input_item_id: str | None = None,
    ) -> ConversationTurn:
        clean_text = text.strip()
        if not clean_text:
            raise InvalidStateError("Transcript cannot be empty")
        session = self.repository.get_session(session_id)
        if session.status is not SessionStatus.ACTIVE:
            raise InvalidStateError("Session is not active")
        self._case_for_session(session)
        reserved_turn_id = turn_id or new_id()
        turn = ConversationTurn(
            id=reserved_turn_id,
            session_id=session.id,
            sequence=len(session.turns) + 1,
            user_text=clean_text,
            patient_text="",
            revealed_fact_ids=(),
            provider_input_item_id=provider_input_item_id,
            provider_response_status="awaiting_response",
            response_state=TurnResponseState.TRANSCRIPT_RESERVED,
        )
        return self.repository.append_turn_idempotent(turn)

    async def complete_turn(self, turn: ConversationTurn) -> TurnOutcome:
        session = self.repository.get_session(turn.session_id)
        if session.status is not SessionStatus.ACTIVE:
            raise InvalidStateError("Session is not active")
        case = self._case_for_session(session)
        try:
            patient: PatientOutcome = await self._patient.respond(
                session=session,
                case=case,
                user_text=turn.user_text,
                turn_id=turn.id,
            )
        except ProviderError as exc:
            self.repository.record_execution(cast(ExecutionRecord, exc.execution))
            self.repository.mark_turn_response_failed(turn.id)
            raise
        self.repository.record_execution(patient.execution)
        turn = self.repository.save_selected_response(
            turn.id,
            patient_text=patient.spoken_text,
            selected_fact_ids=patient.selected_fact_ids,
            provider_response_id=turn.provider_response_id,
            provider_response_status="completed",
            patient_response_kind=patient.response_kind,
        )
        return TurnOutcome(turn=turn, execution=patient.execution)

    async def end_session(self, session_id: str) -> AnalysisOutcome:
        session = self.repository.get_session(session_id)
        if session.status is SessionStatus.COMPLETED:
            return AnalysisOutcome(session=session)
        if session.status not in {
            SessionStatus.ACTIVE,
            SessionStatus.ANALYSIS_PENDING,
            SessionStatus.ANALYSIS_FAILED,
        }:
            raise InvalidStateError(f"Cannot analyze a {session.status} session")
        if not session.turns:
            raise InvalidStateError("At least one final transcript is required")
        case = self._case_for_session(session)
        self.repository.mark_call_ended(session_id)
        self.repository.set_status(session_id, SessionStatus.ANALYSIS_PENDING)
        session = self.repository.get_session(session_id)
        try:
            outcome = await self._evaluator.evaluate(session, case)
            self.repository.record_execution(outcome.execution)
            metrics = self._build_metrics(outcome, case)
            vocabulary = tuple(
                VocabularyObservation(
                    id=new_id(),
                    session_id=session.id,
                    lemma=str(item["lemma"]),
                    translation=str(item["translation"]),
                    example=str(item["example"]),
                    confidence=float(str(item["confidence"])),
                    evidence_turn_sequences=tuple(cast(list[int], item["evidence_turn_sequences"])),
                    state=VocabularyState.IDENTIFIED,
                    kind=str(item.get("kind", "missing")),
                )
                for item in outcome.vocabulary_candidates
            )
            self.repository.save_analysis(session_id, outcome.evaluation, metrics, vocabulary)
            if self._lexicon is not None:
                cadence = self.repository.get_learner(session.learner_id).details
                self._lexicon.ingest_session(
                    session, case, outcome, maintenance_days=cadence.maintenance_cadence_days
                )
        except ProviderError as exc:
            execution = cast(ExecutionRecord, exc.execution)
            self.repository.record_execution(execution)
            self.repository.set_status(session_id, SessionStatus.ANALYSIS_FAILED)
            raise
        except Exception:
            self.repository.set_status(session_id, SessionStatus.ANALYSIS_FAILED)
            raise
        return AnalysisOutcome(session=self.repository.get_session(session_id))

    def _case_for_session(self, session: ConversationSession) -> MedicalCase:
        case = self.cases.get(
            session.case_id,
            session.case_version,
            scenario_id=session.training_snapshot.get("scenario_id"),
            scenario_version=session.training_snapshot.get("scenario_version"),
        )
        if case.content_hash != session.case_hash or dict(case.training_snapshot) != dict(
            session.training_snapshot
        ):
            raise InvalidStateError(
                f"Case {case.id}@{case.version} changed without a version increment"
            )
        return case

    @staticmethod
    def _build_metrics(outcome: EvaluationOutcome, case: MedicalCase) -> SessionMetrics:
        maxima = {criterion.id: float(criterion.max_score) for criterion in case.rubric}
        scores = {
            str(item["criterion_id"]): float(item["score"])
            / maxima.get(str(item["criterion_id"]), 5.0)
            for item in outcome.evaluation.criteria
        }
        return SessionMetrics(
            schema_version="session-metrics-v1",
            clinical_coverage=scores.get("clinical_coverage", 0.0),
            communication=scores.get("communication", 0.0),
            structure=scores.get("structure", 0.0),
            language=scores.get("language", 0.0),
            vocabulary=scores.get("vocabulary", 0.0),
            pronunciation_status="not_assessed",
        )
