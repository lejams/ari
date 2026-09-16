import json
from dataclasses import replace
from typing import Any, cast

from ari.application.contracts import ExecutionContext, LLMRequest
from ari.application.ports.evaluator import EvaluationOutcome
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.application.schemas import EvaluationOutputSchema
from ari.application.services.assessment import weighted_assessment
from ari.application.services.next_actions import next_actions
from ari.application.services.structure import first_delivery_turn, section_coverage
from ari.domain.errors import ProviderError
from ari.domain.models import (
    CodeSwitch,
    ConversationSession,
    Evaluation,
    EvidenceObservation,
    ExecutionStatus,
    MedicalCase,
)

# v3 added code switches and candidate kinds; v4 adds section coverage, empathy judgements
# and next actions. Deterministic criteria and their scoring method are unchanged from v2.
EVALUATION_SCHEMA_VERSION = "session-evaluation-v4"


def empathy_triggers(session: ConversationSession, case: MedicalCase) -> list[dict[str, Any]]:
    """Deterministic part of the empathy judgement: which moment fired, and where.

    trigger_turn: first delivered turn revealing the fact; response_turn: the learner's
    next utterance. The verdict for reachable moments comes from the evaluator.
    """
    first = first_delivery_turn(session)
    sequences = sorted(t.sequence for t in session.turns)
    moments: list[dict[str, Any]] = []
    for moment in case.empathy_moments:
        trigger = first.get(moment.fact_id)
        response = next((s for s in sequences if trigger is not None and s > trigger), None)
        moments.append(
            {
                "moment_id": moment.id,
                "fact_id": moment.fact_id,
                "cue": moment.cue,
                "expected": moment.expected,
                "trigger_turn": trigger,
                "response_turn": response,
                "verdict": (
                    "not_triggered"
                    if trigger is None
                    else "not_reached"
                    if response is None
                    else "pending"
                ),
                "feedback": "",
                "evidence_turn_sequences": [],
            }
        )
    return moments


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
                    "[patient response excluded: failed]"
                    if item.provider_response_status != "completed"
                    else item.patient_text
                ),
                "patient_response_kind": item.patient_response_kind,
                "revealed_fact_ids": list(item.revealed_fact_ids),
            }
            for item in session.turns
        ]
        moments = empathy_triggers(session, case)
        judgeable = {m["moment_id"]: m for m in moments if m["verdict"] == "pending"}
        request = LLMRequest(
            messages=(
                {"role": "system", "content": self._prompt.content},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "simulation_language": case.language,
                            "feedback_language": self._feedback_language,
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
                            "empathy_moments": [
                                {
                                    "id": m["moment_id"],
                                    "trigger_turn": m["trigger_turn"],
                                    "response_turn": m["response_turn"],
                                    "cue": m["cue"],
                                    "expected": m["expected"],
                                }
                                for m in judgeable.values()
                            ],
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
        for code_switch in result.value.code_switches:
            if code_switch.turn not in valid_turns:
                raise reject("Code switch references an unknown transcript turn")
        judged_ids = [judgement.moment_id for judgement in result.value.empathy]
        if set(judged_ids) != set(judgeable) or len(judged_ids) != len(set(judged_ids)):
            raise reject("Empathy judgements do not match the supplied moments")
        for judgement in result.value.empathy:
            moment = judgeable[judgement.moment_id]
            if set(judgement.evidence_turn_sequences) - {
                moment["trigger_turn"],
                moment["response_turn"],
            }:
                raise reject("Empathy judgement references a turn outside the moment")
            moment.update(
                verdict=judgement.verdict,
                feedback=judgement.feedback,
                evidence_turn_sequences=sorted(judgement.evidence_turn_sequences),
            )

        revealed_fact_ids = frozenset(
            fact_id for turn in session.turns for fact_id in turn.revealed_fact_ids
        )
        missed_fact_ids = tuple(sorted(case.fact_ids - revealed_fact_ids))
        criteria_payload = weighted_assessment(session, case)
        overall = float(sum(cast(float, item["score"]) for item in criteria_payload))
        maximum = float(sum(item.max_score for item in case.rubric))
        structure = section_coverage(session, case)
        empathy = tuple(moments)
        candidates = sum(
            1 for item in result.value.vocabulary_candidates if item.kind != "well_used"
        ) + sum(1 for item in result.value.code_switches if item.intended_term)
        evaluation = Evaluation(
            schema_version=EVALUATION_SCHEMA_VERSION,
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
            code_switches=tuple(
                CodeSwitch(item.turn, item.fragment, item.intended_term)
                for item in result.value.code_switches
            ),
            structure=structure,
            empathy=empathy,
            next_actions=next_actions(case, criteria_payload, structure, empathy, candidates),
        )
        return EvaluationOutcome(
            evaluation=evaluation,
            execution=result.execution,
            vocabulary_candidates=tuple(
                item.model_dump() for item in result.value.vocabulary_candidates
            ),
        )
