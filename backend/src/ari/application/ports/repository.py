from typing import Protocol

from ari.domain.models import (
    ConversationSession,
    ConversationTurn,
    Evaluation,
    ExecutionRecord,
    LearnerProfile,
    SessionMetrics,
    SessionStatus,
    VocabularyObservation,
)


class SessionRepository(Protocol):
    def create_learner(self, learner: LearnerProfile) -> LearnerProfile: ...

    def get_learner(self, learner_id: str) -> LearnerProfile: ...

    def update_learner(self, learner: LearnerProfile) -> LearnerProfile: ...

    def create_session(self, session: ConversationSession) -> ConversationSession: ...
    def find_session_request(
        self,
        learner_id: str,
        request_id: str,
    ) -> ConversationSession | None: ...

    def get_session(self, session_id: str) -> ConversationSession: ...

    def list_sessions(self, learner_id: str) -> tuple[ConversationSession, ...]: ...

    def set_status(self, session_id: str, status: SessionStatus) -> None: ...

    def mark_call_started(self, session_id: str) -> None: ...

    def mark_call_ended(self, session_id: str) -> None: ...

    def append_turn(self, turn: ConversationTurn) -> None: ...

    def append_turn_idempotent(self, turn: ConversationTurn) -> ConversationTurn: ...

    def save_selected_response(
        self,
        turn_id: str,
        *,
        patient_text: str,
        selected_fact_ids: tuple[str, ...],
        provider_response_id: str | None,
        provider_response_status: str,
    ) -> ConversationTurn: ...

    def mark_turn_response_failed(self, turn_id: str) -> ConversationTurn: ...

    def begin_audio_stream(
        self, session_id: str, turn_id: str, audio_stream_id: str
    ) -> ConversationTurn: ...

    def mark_audio_sent(
        self, session_id: str, turn_id: str, audio_stream_id: str, chunk_count: int
    ) -> ConversationTurn: ...

    def mark_tts_failed(
        self, session_id: str, turn_id: str, audio_stream_id: str
    ) -> ConversationTurn: ...

    def confirm_audio_started(
        self,
        session_id: str,
        turn_id: str,
        audio_stream_id: str,
        *,
        provider_response_id: str | None,
        last_index: int,
    ) -> ConversationTurn: ...

    def confirm_audio_delivered(
        self,
        session_id: str,
        turn_id: str,
        audio_stream_id: str,
        *,
        provider_response_id: str | None,
        last_index: int,
    ) -> ConversationTurn: ...

    def mark_session_delivery_unconfirmed(self, session_id: str) -> None: ...

    def get_turn_by_provider_input(
        self, session_id: str, provider_input_item_id: str
    ) -> ConversationTurn | None: ...

    def get_turn_by_provider_response(
        self, session_id: str, provider_response_id: str
    ) -> ConversationTurn | None: ...

    def record_execution(self, execution: ExecutionRecord) -> None: ...

    def save_analysis(
        self,
        session_id: str,
        evaluation: Evaluation,
        metrics: SessionMetrics,
        vocabulary: tuple[VocabularyObservation, ...],
    ) -> None: ...
