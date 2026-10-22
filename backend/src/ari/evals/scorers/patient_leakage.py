"""`patient-leakage`: does the patient reveal only what the doctor asked, in the right language?

One item is one doctor utterance after an optional history. The annotation says which kind
of answer is expected and which facts the question legitimately asks for. Run through the
real patient of the configured engine, so its guards apply: `PatientSimulator` (training) or
`ExamPatient` (exam chain, `patient_engine: exam`). On the exam chain, the three non-clinical
kinds (small talk, clarification request, hesitation) reveal no fact: they count as a refusal
of an off-topic utterance and as a refusal of a legitimate question. The rate of free wordings
the exam screen rejected (`free_text_rejection_rate`) is reported, not gated.

Outcomes:
- `fact_leak`: a fact outside `allowed_fact_ids` was selected (revealed without a question);
- `invented_fact`: the model referenced a source the case does not contain, on any attempt;
- `refusal_missed`: an out-of-scope or other-language utterance was answered;
- `over_refusal`: a legitimate patient question was refused;
- `answer_incomplete`: an expected fact was not selected (informational, not a failure).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import timedelta
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ari.application.ports.llm import LLMProvider
from ari.application.schemas import NON_CLINICAL_KINDS, PatientResponseSchema
from ari.application.services.exam_session import ExamPatient
from ari.application.services.patient import PatientOutcome, PatientSimulator
from ari.domain.models import (
    AudioDeliveryStatus,
    ConversationSession,
    ConversationTurn,
    LearningGoal,
    MedicalCase,
    utc_now,
)
from ari.evals.metrics import Counts, Ratio
from ari.evals.scorers import CallRecord, EvalItem, ItemScore, Reliability, RunContext

ResponseKind = Literal["sources", "unknown", "out_of_scope", "wrong_language"]
REFUSALS = frozenset({"out_of_scope", "wrong_language"})
# Answers that reveal nothing and are not "unknown": a refusal, or a non-clinical exam turn.
NON_ANSWERS = REFUSALS | frozenset(NON_CLINICAL_KINDS)


class HistoryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doctor: Annotated[str, Field(min_length=1)]
    revealed_fact_ids: list[str] = Field(default_factory=list)


class LeakageItem(EvalItem):
    utterance: Annotated[str, Field(min_length=1)]
    history: list[HistoryTurn] = Field(default_factory=list)
    elapsed_seconds: Annotated[int, Field(ge=0)] = 0
    # Kinds of answer the annotator accepts; refusal kinds only means "must be refused".
    expected_kinds: list[ResponseKind] = Field(min_length=1)
    # Facts the question legitimately asks for; anything else selected is a leak.
    allowed_fact_ids: list[str] = Field(default_factory=list)
    # Subset that a complete answer contains (informational recall).
    expected_fact_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self) -> LeakageItem:
        if set(self.expected_fact_ids) - set(self.allowed_fact_ids):
            raise ValueError(f"{self.id}: expected facts must be allowed")
        if "sources" not in self.expected_kinds and self.expected_fact_ids:
            raise ValueError(f"{self.id}: only a sources answer expects facts")
        if set(self.expected_kinds) & REFUSALS and set(self.expected_kinds) - REFUSALS:
            raise ValueError(f"{self.id}: an utterance is either refused or answered")
        return self

    @property
    def refusal_expected(self) -> bool:
        return set(self.expected_kinds) <= REFUSALS


class PatientLeakageSuite:
    name: str = "patient-leakage"
    scorer_version: str = "patient-leakage-scorer-v1"
    prompt_roles: tuple[str, ...] = ("patient",)
    output_schemas: tuple[type[BaseModel], ...] = (PatientResponseSchema,)

    def parse_item(self, row: Mapping[str, object]) -> LeakageItem:
        # Through JSON so strict enum-like fields accept their string values.
        return LeakageItem.model_validate_json(json.dumps(row))

    def validate_against(self, item: LeakageItem, case: MedicalCase) -> None:
        referenced = {
            *item.allowed_fact_ids,
            *(f for turn in item.history for f in turn.revealed_fact_ids),
        }
        unknown = referenced - case.fact_ids
        if unknown:
            raise ValueError(f"{item.id}: unknown fact ids {sorted(unknown)}")

    async def execute(
        self, item: EvalItem, case: MedicalCase, provider: LLMProvider, context: RunContext
    ) -> PatientOutcome:
        leakage = cast(LeakageItem, item)
        self.validate_against(leakage, case)
        phrases = {fact.id: fact.patient_phrase for fact in case.facts}
        turns = tuple(
            ConversationTurn(
                id=f"{context.run_key}:t{index}",
                session_id=context.run_key,
                sequence=index,
                user_text=turn.doctor,
                patient_text=" ".join(phrases[f] for f in turn.revealed_fact_ids)
                or case.unknown_response,
                revealed_fact_ids=tuple(turn.revealed_fact_ids),
                delivery_status=AudioDeliveryStatus.DELIVERED,
            )
            for index, turn in enumerate(leakage.history, start=1)
        )
        session = ConversationSession(
            id=context.run_key,
            learner_id="evals",
            case_id=case.id,
            case_version=case.version,
            case_hash=case.content_hash,
            goal=LearningGoal(),
            turns=turns,
            call_started_at=utc_now() - timedelta(seconds=leakage.elapsed_seconds),
        )
        prompt = context.prompts["patient"]
        patient = (
            ExamPatient(provider, prompt)
            if context.patient_engine == "exam"
            else PatientSimulator(provider, prompt)
        )
        return await patient.respond(
            session=session,
            case=case,
            user_text=leakage.utterance,
            turn_id=f"{context.run_key}:t{len(turns) + 1}",
        )

    def score(
        self,
        item: EvalItem,
        case: MedicalCase,
        value: object | None,
        calls: Sequence[CallRecord],
    ) -> ItemScore:
        leakage = cast(LeakageItem, item)
        known_refs = {"opening_statement", *(f"fact:{f}" for f in case.fact_ids)}
        invented = sorted(
            {
                ref
                for call in calls
                if isinstance(call.value, PatientResponseSchema)
                for ref in call.value.source_refs
                if ref not in known_refs
            }
        )
        counts: dict[str, float] = {"turns": 1.0, "invented_turns": 1.0 if invented else 0.0}
        outcomes: list[str] = ["invented_fact"] if invented else []
        refusal_expected = leakage.refusal_expected
        counts["refusal.expected"] = 1.0 if refusal_expected else 0.0
        counts["question.expected"] = 0.0 if refusal_expected else 1.0
        if value is None:
            # No usable answer even after a retry: counted by the runner, not as a leak.
            return ItemScore(
                verdict=False,
                outcomes=tuple(outcomes),
                counts=counts,
                signature=json.dumps({"usable": False, "invented": invented}),
                detail={"expected_kinds": leakage.expected_kinds, "invented_refs": invented},
            )
        outcome = cast(PatientOutcome, value)
        selected = set(outcome.selected_fact_ids)
        leaked = sorted(selected - set(leakage.allowed_fact_ids))
        missing = sorted(set(leakage.expected_fact_ids) - selected)
        refused = outcome.response_kind in NON_ANSWERS
        free_text = outcome.execution.usage.get("free_text")
        counts["free_text.spoken_or_rejected"] = 1.0 if free_text else 0.0
        counts["free_text.rejected"] = 1.0 if free_text == "rejected" else 0.0
        counts["leak_turns"] = 1.0 if leaked else 0.0
        counts["leaked_facts"] = float(len(leaked))
        counts["refusal.correct"] = (
            1.0 if refusal_expected and outcome.response_kind in leakage.expected_kinds else 0.0
        )
        counts["refusal.refused"] = 1.0 if refusal_expected and refused else 0.0
        counts["question.refused"] = 1.0 if not refusal_expected and refused else 0.0
        counts["answer.expected_facts"] = float(len(leakage.expected_fact_ids))
        counts["answer.found_facts"] = float(len(leakage.expected_fact_ids) - len(missing))
        if leaked:
            outcomes.append("fact_leak")
        if refusal_expected and not refused:
            outcomes.append("refusal_missed")
        if not refusal_expected and refused:
            outcomes.append("over_refusal")
        verdict = not outcomes
        if missing:
            outcomes.append("answer_incomplete")
        return ItemScore(
            verdict=verdict,
            outcomes=tuple(outcomes),
            counts=counts,
            signature=json.dumps(
                {"kind": outcome.response_kind, "facts": sorted(selected), "invented": invented}
            ),
            detail={
                "utterance": leakage.utterance,
                "expected_kinds": leakage.expected_kinds,
                "response_kind": outcome.response_kind,
                "selected_fact_ids": sorted(selected),
                "leaked_fact_ids": leaked,
                "missing_fact_ids": missing,
                "invented_refs": invented,
            },
        )

    def ratios(self, keys: Iterable[str]) -> list[Ratio]:
        del keys
        return [
            Ratio("leak_rate", ("leak_turns",), ("turns",)),
            Ratio("invented_fact_rate", ("invented_turns",), ("turns",)),
            # Refused with the exact expected kind (out_of_scope vs wrong_language).
            Ratio("refusal.recall", ("refusal.correct",), ("refusal.expected",)),
            # Refused at all, whatever the kind: what the learner experiences.
            Ratio("refusal.any_recall", ("refusal.refused",), ("refusal.expected",)),
            Ratio("over_refusal_rate", ("question.refused",), ("question.expected",)),
            Ratio("answer.recall", ("answer.found_facts",), ("answer.expected_facts",)),
            Ratio(
                "free_text_rejection_rate",
                ("free_text.rejected",),
                ("free_text.spoken_or_rejected",),
            ),
        ]

    def reliability(self, counts: Counts, producers: Mapping[str, str]) -> list[Reliability]:
        # The patient emits no learner observation.
        del counts, producers
        return []
