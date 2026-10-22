"""`evaluation-medical`: does the session evaluation catch what the physician flagged?

THE DATASET DOES NOT EXIST YET: it needs 30 to 50 learner transcripts annotated by the
physician, with consent (plan, action 8). This module only fixes the annotation format and
the scoring, so the annotations plug in without code. Nothing here states a medical truth:
every expectation comes from the physician's annotation of the item.

What is compared:
- required omissions: the assessment items the physician says the learner failed to cover,
  against the deterministic `assessment-weighted-v1` result (`missing_required_item_ids`);
- medical errors in what the learner said: the evaluation output of `session-evaluation-v4`
  has no field for them, so they are counted as not detected. The suite makes that gap
  measurable instead of hiding it.
A critical item is one the physician marked `critical`; a missed one is a critical error.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, cast

from pydantic import BaseModel, ConfigDict, Field

from ari.application.ports.evaluator import EvaluationOutcome
from ari.application.ports.llm import LLMProvider
from ari.application.schemas import EvaluationOutputSchema
from ari.application.services.evaluation import LLMBackedEvaluator
from ari.domain.models import CEFRLevel, MedicalCase
from ari.evals.metrics import Counts, Ratio, precision_recall
from ari.evals.scorers import CallRecord, EvalItem, ItemScore, Reliability, RunContext
from ari.evals.scorers.transcripts import (
    TranscriptTurn,
    check_transcript,
    session_from_transcript,
)

DETERMINISTIC_PRODUCER = "assessment-weighted-v1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnnotatedOmission(_Strict):
    item_id: Annotated[str, Field(min_length=1)]  # An assessment item of the case.
    critical: bool


class AnnotatedMedicalError(_Strict):
    turn: Annotated[int, Field(ge=1)]
    description: Annotated[str, Field(min_length=1)]
    critical: bool


class MedicalAnnotation(_Strict):
    annotator: Annotated[str, Field(min_length=1)]
    required_omissions: list[AnnotatedOmission] = Field(default_factory=list)
    medical_errors: list[AnnotatedMedicalError] = Field(default_factory=list)


class MedicalItem(EvalItem):
    turns: list[TranscriptTurn] = Field(min_length=1)
    target_cefr: CEFRLevel = CEFRLevel.B2
    annotation: MedicalAnnotation


class EvaluationMedicalSuite:
    name: str = "evaluation-medical"
    scorer_version: str = "evaluation-medical-scorer-v1"
    prompt_roles: tuple[str, ...] = ("evaluation",)
    output_schemas: tuple[type[BaseModel], ...] = (EvaluationOutputSchema,)

    def parse_item(self, row: Mapping[str, object]) -> MedicalItem:
        return MedicalItem.model_validate_json(json.dumps(row))

    async def execute(
        self, item: EvalItem, case: MedicalCase, provider: LLMProvider, context: RunContext
    ) -> EvaluationOutcome:
        medical = cast(MedicalItem, item)
        check_transcript(item.id, medical.turns, case)
        known = {i.id for i in case.assessment_items}
        unknown = {o.item_id for o in medical.annotation.required_omissions} - known
        if unknown:
            raise ValueError(f"{item.id}: unknown assessment items {sorted(unknown)}")
        session = session_from_transcript(context.run_key, case, medical.turns, medical.target_cefr)
        evaluator = LLMBackedEvaluator(
            provider, context.prompts["evaluation"], context.feedback_language
        )
        return await evaluator.evaluate(session, case)

    def score(
        self,
        item: EvalItem,
        case: MedicalCase,
        value: object | None,
        calls: Sequence[CallRecord],
    ) -> ItemScore:
        del case, calls
        annotation = cast(MedicalItem, item).annotation
        critical_omissions = {o.item_id for o in annotation.required_omissions if o.critical}
        expected = {o.item_id for o in annotation.required_omissions}
        counts: dict[str, float] = {
            "critical.annotated": float(
                len(critical_omissions) + sum(e.critical for e in annotation.medical_errors)
            ),
            "statement.annotated": float(len(annotation.medical_errors)),
            "statement.detected": 0.0,  # No output channel in session-evaluation-v4.
        }
        if value is None:
            # Every annotated omission was missed: it stays in the recall denominator.
            counts["omission.fn.all"] = float(len(expected))
            return ItemScore(False, (), counts, json.dumps({"usable": False}))
        evaluation = cast(EvaluationOutcome, value).evaluation
        reported = {
            str(missing)
            for criterion in evaluation.criteria
            for missing in cast(list[str], criterion.get("missing_required_item_ids", []))
        }
        counts["omission.tp.all"] = float(len(reported & expected))
        counts["omission.fp.all"] = float(len(reported - expected))
        counts["omission.fn.all"] = float(len(expected - reported))
        counts["critical.detected"] = float(len(critical_omissions & reported))
        outcomes: set[str] = set()
        if critical_omissions - reported or any(e.critical for e in annotation.medical_errors):
            outcomes.add("missed_critical_medical_error")
        if (expected - critical_omissions) - reported or any(
            not e.critical for e in annotation.medical_errors
        ):
            outcomes.add("missed_medical_error")
        if reported - expected:
            outcomes.add("false_omission")
        return ItemScore(
            verdict=not outcomes,
            outcomes=tuple(sorted(outcomes)),
            counts=counts,
            signature=json.dumps(sorted(reported)),
            detail={
                "reported_omissions": sorted(reported),
                "annotated_omissions": sorted(expected),
                "annotated_medical_errors": [e.model_dump() for e in annotation.medical_errors],
            },
        )

    def ratios(self, keys: Iterable[str]) -> list[Ratio]:
        del keys
        return [
            Ratio("critical_medical_error.recall", ("critical.detected",), ("critical.annotated",)),
            *precision_recall("omission", "omission", "all"),
            Ratio("statement_error.recall", ("statement.detected",), ("statement.annotated",)),
        ]

    def reliability(self, counts: Counts, producers: Mapping[str, str]) -> list[Reliability]:
        del producers
        tp, fp = counts.get("omission.tp.all", 0.0), counts.get("omission.fp.all", 0.0)
        return [
            Reliability(
                source="deterministic",
                producer=DETERMINISTIC_PRODUCER,
                precision=tp / (tp + fp) if tp + fp else None,
                observations=int(tp + fp),
            )
        ]
