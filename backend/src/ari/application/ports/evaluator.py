from dataclasses import dataclass
from typing import Protocol

from ari.domain.models import ConversationSession, Evaluation, ExecutionRecord, MedicalCase


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    evaluation: Evaluation
    execution: ExecutionRecord
    vocabulary_candidates: tuple[dict[str, object], ...]


class Evaluator(Protocol):
    async def evaluate(
        self, session: ConversationSession, case: MedicalCase
    ) -> EvaluationOutcome: ...
