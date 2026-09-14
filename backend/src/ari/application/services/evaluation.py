import json
from dataclasses import replace
from typing import cast

from ari.application.contracts import ExecutionContext, LLMRequest
from ari.application.ports.evaluator import EvaluationOutcome
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.application.schemas import EvaluationOutputSchema
from ari.application.services.assessment import weighted_assessment
from ari.domain.errors import ProviderError
from ari.domain.models import (
    ConversationSession,
    Evaluation,
    EvidenceObservation,
    ExecutionStatus,
    MedicalCase,
)


class LLMBackedEvaluator:
    def __init__(
        self, provider: LLMProvider, prompt: VersionedPrompt, feedback_language: str
    ) -> None:
        self._provider = provider
        self._prompt = prompt
        self._feedback_language = feedback_language

    async def evaluate(self, session: ConversationSession, case: MedicalCase) -> EvaluationOutcome:
        transcript = [
            {
                "turn": item.sequence,
                "doctor": item.user_text,
                "patient": (
                    "[patient response excluded: interrupted]"
                    if item.interrupted or item.provider_response_status != "completed"
                    else item.patient_text
                ),
                "revealed_fact_ids": list(item.revealed_fact_ids),
            }
            for item in session.turns
        ]
        request = LLMRequest(
            messages=(
                {"role": "system", "content": self._prompt.content},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "simulation_language": case.language,
                            "feedback_language": self._feedback_language,
                            "case_mode": case.mode.value,
                            "goal": {
                                "exam": session.goal.target_exam,
                                "cefr": session.goal.target_cefr.value,
                            },
                            "rubric": [
                                {
                                    "id": item.id,
                                    "label": item.label,
                                    "max_score": item.max_score,
                                    "description": item.description,
                                }
                                for item in case.rubric
                            ],
                            "case_facts": [
                                {
                                    "id": fact.id,
                                    "category": fact.category,
                                    "value": fact.value,
                                    "disclosure": fact.disclosure.value,
                                }
                                for fact in case.facts
                            ],
                            "transcript": transcript,
                        },
                        ensure_ascii=False,
                    ),
                },
            ),
            context=ExecutionContext(
                session_id=session.id,
                learner_id=session.learner_id,
                operation="session_evaluation",
                case_version=case.version,
                case_hash=case.content_hash,
                prompt_version=self._prompt.version,
                prompt_hash=self._prompt.content_hash,
            ),
        )
        result = await self._provider.generate_structured(request, EvaluationOutputSchema)

        def reject(message: str) -> ProviderError:
            return ProviderError(
                message,
                execution=replace(
                    result.execution,
                    status=ExecutionStatus.FAILED,
                    error_code="structured_output_guard",
                    error_message=message,
                ),
            )

        allowed_criteria = {item.id: item for item in case.rubric}
        result_ids = [item.criterion_id for item in result.value.criteria]
        if set(result_ids) != set(allowed_criteria) or len(result_ids) != len(set(result_ids)):
            raise reject("Evaluation criteria do not match the case rubric")
        valid_turns = {item.sequence for item in session.turns}
        for item in result.value.criteria:
            if set(item.evidence_turn_sequences) - valid_turns:
                raise reject("Evaluation references an unknown transcript turn")
            if item.score > allowed_criteria[item.criterion_id].max_score:
                raise reject("Evaluation score exceeds rubric maximum")

        for observations in (
            result.value.strengths,
            result.value.priorities,
            result.value.language_errors,
            result.value.vocabulary_candidates,
        ):
            for observation in observations:
                if set(observation.evidence_turn_sequences) - valid_turns:
                    raise reject("Evaluation observation references an unknown transcript turn")

        revealed_fact_ids = frozenset(
            fact_id for turn in session.turns for fact_id in turn.revealed_fact_ids
        )
        missed_fact_ids = tuple(sorted(case.fact_ids - revealed_fact_ids))
        criteria_payload: list[dict[str, object]] = []
        for item in result.value.criteria:
            payload = item.model_dump()
            if item.criterion_id == "clinical_coverage":
                criterion = allowed_criteria[item.criterion_id]
                ratio = len(revealed_fact_ids) / len(case.facts) if case.facts else 0.0
                payload["score"] = min(criterion.max_score, int(ratio * criterion.max_score + 0.5))
                payload["evidence_turn_sequences"] = [
                    turn.sequence for turn in session.turns if turn.revealed_fact_ids
                ] or [session.turns[0].sequence]
                payload["feedback"] = (
                    f"Couverture clinique calculée : {len(revealed_fact_ids)}/"
                    f"{len(case.facts)} faits du cas obtenus."
                )
            criteria_payload.append(payload)

        if case.schema_version == "clinical-case-v2":
            criteria_payload = weighted_assessment(session, case)
        overall = float(sum(cast(float, item["score"]) for item in criteria_payload))
        maximum = float(sum(item.max_score for item in case.rubric))
        evaluation = Evaluation(
            schema_version=("session-evaluation-v2" if case.schema_version == "clinical-case-v2"
                            else "session-evaluation-v1"),
            prompt_version=self._prompt.version,
            rubric_version=case.rubric_version,
            overall_score=overall,
            max_score=maximum,
            summary=result.value.summary,
            strengths=tuple(
                EvidenceObservation(item.text, tuple(item.evidence_turn_sequences))
                for item in result.value.strengths
            ),
            priorities=tuple(
                EvidenceObservation(item.text, tuple(item.evidence_turn_sequences))
                for item in result.value.priorities
            ),
            missed_fact_ids=missed_fact_ids,
            language_errors=tuple(
                EvidenceObservation(item.text, tuple(item.evidence_turn_sequences))
                for item in result.value.language_errors
            ),
            criteria=tuple(criteria_payload),
        )
        return EvaluationOutcome(
            evaluation=evaluation,
            execution=result.execution,
            vocabulary_candidates=tuple(
                item.model_dump() for item in result.value.vocabulary_candidates
            ),
        )
