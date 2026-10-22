"""`evaluation-language`: does the session evaluator find the language errors, and only them?

One item is one synthetic transcript in which errors were injected at known turns, so the
truth is known by construction: one language error per modified turn with its category,
French fragments (code switches), speech-to-text artefacts that the learner did not make
(spelling, capitalisation, punctuation, split compounds), and sections left out.
Run through the real `LLMBackedEvaluator`, with its evidence guards.

Matching: a reported error is correct when it cites the modified turn with the injected
category. A report citing a French turn is neutral (the fragment belongs to code switches,
whatever category is given). Any other report is a false error; it is also a hallucinated
evidence when none of the turns it cites carries an injected language error.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ari.application.ports.evaluator import EvaluationOutcome
from ari.application.ports.llm import LLMProvider
from ari.application.schemas import LANGUAGE_ERROR_CATEGORIES, EvaluationOutputSchema
from ari.application.services.evaluation import LLMBackedEvaluator
from ari.domain.clinical import AnamnesisSectionId
from ari.domain.models import CEFRLevel, MedicalCase
from ari.evals.metrics import Counts, Ratio, precision_recall, suffixes
from ari.evals.scorers import CallRecord, EvalItem, ItemScore, Reliability, RunContext
from ari.evals.scorers.transcripts import (
    TranscriptTurn,
    check_transcript,
    session_from_transcript,
)

Category = Annotated[str, Field(pattern="^(" + "|".join(LANGUAGE_ERROR_CATEGORIES) + ")$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InjectedError(_Strict):
    turn: Annotated[int, Field(ge=1)]
    category: Category
    correct: Annotated[str, Field(min_length=1)]  # The utterance before injection.


class InjectedCodeSwitch(_Strict):
    turn: Annotated[int, Field(ge=1)]
    intended_term: Annotated[str, Field(min_length=1)]


class LanguageTruth(_Strict):
    language_errors: list[InjectedError] = Field(default_factory=list)
    code_switches: list[InjectedCodeSwitch] = Field(default_factory=list)
    stt_artifact_turns: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    missing_sections: list[AnamnesisSectionId] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_change_per_turn(self) -> LanguageTruth:
        changed = [
            *(e.turn for e in self.language_errors),
            *(c.turn for c in self.code_switches),
            *self.stt_artifact_turns,
        ]
        if len(changed) != len(set(changed)):
            raise ValueError("at most one injected change per turn")
        return self


class LanguageItem(EvalItem):
    turns: list[TranscriptTurn] = Field(min_length=1)
    target_cefr: CEFRLevel = CEFRLevel.B2
    truth: LanguageTruth


class EvaluationLanguageSuite:
    name: str = "evaluation-language"
    scorer_version: str = "evaluation-language-scorer-v1"
    prompt_roles: tuple[str, ...] = ("evaluation",)
    output_schemas: tuple[type[BaseModel], ...] = (EvaluationOutputSchema,)

    def parse_item(self, row: Mapping[str, object]) -> LanguageItem:
        return LanguageItem.model_validate_json(json.dumps(row))

    async def execute(
        self, item: EvalItem, case: MedicalCase, provider: LLMProvider, context: RunContext
    ) -> EvaluationOutcome:
        language = cast(LanguageItem, item)
        check_transcript(item.id, language.turns, case)
        session = session_from_transcript(
            context.run_key, case, language.turns, language.target_cefr
        )
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
        language = cast(LanguageItem, item)
        truth = language.truth
        counts: dict[str, float] = {}

        def count(key: str, amount: float = 1.0) -> None:
            counts[key] = counts.get(key, 0.0) + amount

        for error in truth.language_errors:
            count(f"lang.injected.{error.category}")
        if value is None:
            # No usable evaluation even after a retry: every annotated error was missed, so it
            # stays in the recall denominators instead of vanishing from them.
            for error in truth.language_errors:
                count(f"lang.fn.{error.category}")
            count("cs.fn.all", len({c.turn for c in truth.code_switches}))
            count("section.fn.all", len(set(truth.missing_sections)))
            return ItemScore(
                verdict=False,
                outcomes=(),
                counts=counts,
                signature=json.dumps({"usable": False}),
            )
        evaluation = cast(EvaluationOutcome, value).evaluation
        error_turns = {e.turn for e in truth.language_errors}
        switch_turns = {c.turn for c in truth.code_switches}
        stt_turns = set(truth.stt_artifact_turns)
        unmatched = list(truth.language_errors)
        predicted: list[dict[str, object]] = []
        outcomes: set[str] = set()
        for observation in evaluation.language_errors:
            category = observation.category or "other"
            cited = set(observation.evidence_turn_sequences)
            match = next((e for e in unmatched if e.turn in cited and e.category == category), None)
            if match is not None:
                unmatched.remove(match)
                count(f"lang.tp.{category}")
                verdict = "correct"
            elif cited & switch_turns:
                count("lang.neutral")
                verdict = "neutral_code_switch"
            else:
                count(f"lang.fp.{category}")
                outcomes.add("false_language_error")
                if cited & error_turns:
                    count("lang.confused")
                    verdict = "wrong_category"
                else:
                    count("lang.hallucinated")
                    verdict = "no_error_in_cited_turns"
            if verdict != "neutral_code_switch":
                count("lang.reported")
            predicted.append(
                {
                    "category": category,
                    "turns": sorted(cited),
                    "text": observation.text,
                    "verdict": verdict,
                }
            )
        for error in unmatched:
            count(f"lang.fn.{error.category}")
            outcomes.add("missed_language_error")
        blamed = {
            turn
            for observation in evaluation.language_errors
            for turn in observation.evidence_turn_sequences
            if turn in stt_turns
        }
        count("stt.turns", len(stt_turns))
        count("stt.blamed", len(blamed))
        if blamed:
            outcomes.add("stt_artifact_blamed")

        reported_switches = {c.turn for c in evaluation.code_switches}
        count("cs.tp.all", len(reported_switches & switch_turns))
        count("cs.fp.all", len(reported_switches - switch_turns))
        count("cs.fn.all", len(switch_turns - reported_switches))
        if switch_turns - reported_switches:
            outcomes.add("missed_code_switch")
        if reported_switches - switch_turns:
            outcomes.add("false_code_switch")

        # Deterministic part of the evaluation (anamnesis-sections-v1), checked all the same.
        sections = (evaluation.structure or {}).get("sections", [])
        forgotten = {s["id"] for s in sections if not s["covered_fact_ids"]}
        expected = set(truth.missing_sections)
        count("section.tp.all", len(forgotten & expected))
        count("section.fp.all", len(forgotten - expected))
        count("section.fn.all", len(expected - forgotten))
        if forgotten != expected:
            outcomes.add("section_mismatch")

        signature = {
            "errors": sorted(
                f"{o.category or 'other'}@{sorted(o.evidence_turn_sequences)}"
                for o in evaluation.language_errors
            ),
            "code_switches": sorted(reported_switches),
        }
        return ItemScore(
            verdict=not outcomes,
            outcomes=tuple(sorted(outcomes)),
            counts=counts,
            signature=json.dumps(signature),
            detail={
                "reported_errors": predicted,
                "missed_errors": [e.model_dump() for e in unmatched],
                "reported_code_switches": sorted(reported_switches),
                "expected_code_switches": sorted(switch_turns),
                "stt_artifact_turns_blamed": sorted(blamed),
                "forgotten_sections": sorted(forgotten),
                "expected_missing_sections": sorted(expected),
            },
        )

    def ratios(self, keys: Iterable[str]) -> list[Ratio]:
        keys = list(keys)
        categories = sorted(
            {*suffixes(keys, "lang.tp"), *suffixes(keys, "lang.fp"), *suffixes(keys, "lang.fn")}
            | set(suffixes(keys, "lang.injected"))
        )
        tps = tuple(f"lang.tp.{c}" for c in categories)
        fps = tuple(f"lang.fp.{c}" for c in categories)
        fns = tuple(f"lang.fn.{c}" for c in categories)
        return [
            *(r for c in categories for r in precision_recall("language", "lang", c)),
            Ratio("language_overall.precision", tps, tps + fps),
            Ratio("language_overall.recall", tps, tps + fns),
            Ratio("stt_false_error_rate", ("stt.blamed",), ("stt.turns",)),
            Ratio("evidence_hallucination_rate", ("lang.hallucinated",), ("lang.reported",)),
            Ratio("category_confusion_rate", ("lang.confused",), ("lang.reported",)),
            *precision_recall("code_switch", "cs", "all"),
            *precision_recall("forgotten_section", "section", "all"),
        ]

    def reliability(self, counts: Counts, producers: Mapping[str, str]) -> list[Reliability]:
        tp = sum(v for k, v in counts.items() if k.startswith("lang.tp."))
        fp = sum(v for k, v in counts.items() if k.startswith("lang.fp."))
        return [
            Reliability(
                source="llm",
                producer=producers["evaluation"],
                precision=tp / (tp + fp) if tp + fp else None,
                observations=int(tp + fp),
            )
        ]
