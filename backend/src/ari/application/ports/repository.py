from collections.abc import Mapping
from typing import Any, Protocol

from ari.domain.models import (
    ConversationSession,
    ConversationTurn,
    Evaluation,
    ExecutionRecord,
    LearnerProfile,
    PatientOpening,
    SessionMetrics,
    SessionStatus,
    VocabularyHintUsage,
    VocabularyObservation,
    VoiceTurnMetric,
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
        interrupted: bool = False,
        interruption_audio_end_ms: int | None = None,
        normalized_user_text: str | None = None,
        canonical_response: Mapping[str, Any] | None = None,
        observed_response_text: str | None = None,
    ) -> ConversationTurn: ...

    def mark_turn_response_failed(self, turn_id: str) -> ConversationTurn: ...

    def record_observed_response(
        self, turn_id: str, observed_text: str, *, fidelity_matches: bool
    ) -> ConversationTurn: ...

    def finalize_transport_response(
        self,
        turn_id: str,
        *,
        provider_response_id: str,
        provider_response_status: str,
        canonical_response: Mapping[str, Any] | None,
        observed_response_text: str | None,
        fidelity_matches: bool,
        interrupted: bool = False,
        interruption_audio_end_ms: int | None = None,
    ) -> ConversationTurn: ...

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

    def switch_voice_stack_before_first_turn(
        self,
        session_id: str,
        *,
        expected_stack_id: str,
        target_stack_id: str,
        target_stack_version: str,
        target_stack_config: Mapping[str, Any],
        reason: str,
    ) -> ConversationSession: ...

    def get_turn_by_provider_input(
        self, session_id: str, provider_input_item_id: str
    ) -> ConversationTurn | None: ...

    def get_turn_by_provider_response(
        self, session_id: str, provider_response_id: str
    ) -> ConversationTurn | None: ...

    def record_execution(self, execution: ExecutionRecord) -> None: ...

    def record_voice_turn_metric(self, metric: VoiceTurnMetric) -> None: ...

    def get_voice_turn_metric(self, turn_id: str) -> VoiceTurnMetric | None: ...

    def list_voice_turn_metrics(
        self, session_id: str | None = None
    ) -> tuple[VoiceTurnMetric, ...]: ...

    def save_patient_opening(self, opening: PatientOpening) -> PatientOpening: ...

    def record_vocabulary_hint_usage(
        self, session_id: str, hint_id: str, asset_version: str
    ) -> VocabularyHintUsage: ...

    def save_analysis(
        self,
        session_id: str,
        evaluation: Evaluation,
        metrics: SessionMetrics,
        vocabulary: tuple[VocabularyObservation, ...],
    ) -> None: ...
