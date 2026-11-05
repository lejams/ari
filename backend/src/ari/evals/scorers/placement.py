"""`placement`: does the spoken placement rater find the level and the errors it should?

One item is one transcript of a spoken placement task, written at a known level with errors
injected at known places (category, quote, correction), so the truth is known by construction.
Run through the real rater of the placement service (`rate_spoken_task`, prompt
`placement-speaking-v3`: criterion scores, level read by the code), with its code verification
of the quotes.

Matching: a reported error is right when it has the injected category and its quote overlaps the
words of an injected quote in the transcript. A report that overlaps an injected error with another
category is a false one (a confusion); a report that overlaps none is a false one too.

Weak points are not asked of the model: the application computes them from the verified errors
(`weak_points`). The suite compares the categories the application would report with those the
injected errors call for, which is what the learner finally sees.

The level is judged to one level (`level.within_one`), the exact level is only reported: the
injected errors can lower the estimate by one level, and the doctor's FSP does not need more.

Stability is measured on each part of the rating the product uses (`facets`): the level, the
weak points, each error. The whole signature (all of them identical) is still reported, but an
item of six errors rarely stays identical even when each error is found almost every time.
The level is read by the code from a score on a fixed grid, so its stability is measured on that
score (`measures`, `stability.level_score.mean_sd`, in levels): a transcript whose score sits on
a threshold flips level with any rater, however small the noise.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, cast

from pydantic import BaseModel, ConfigDict, Field

from ari.application.contracts import ExecutionContext
from ari.application.ports.llm import LLMProvider
from ari.application.schemas import SpeakingRatingSchema
from ari.application.services.language_errors import spans
from ari.application.services.placement_speaking import SpokenRating, rate_spoken_task
from ari.domain.models import MedicalCase
from ari.domain.placement import LEVELS, PLACEMENT_CATEGORIES, weak_points
from ari.evals.metrics import Counts, Ratio, precision_recall, suffixes
from ari.evals.scorers import CallRecord, EvalItem, ItemScore, Reliability, RunContext

Category = Annotated[str, Field(pattern="^(" + "|".join(PLACEMENT_CATEGORIES) + ")$")]
Level = Annotated[str, Field(pattern="^(A1|A2|B1|B2|C1|C2)$")]
TEST_LANGUAGE = "de-DE"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InjectedSpokenError(_Strict):
    category: Category
    quote: Annotated[str, Field(min_length=1)]  # As spoken: stands once in the transcript.
    correction: Annotated[str, Field(min_length=1)]


class SpokenTruth(_Strict):
    errors: list[InjectedSpokenError] = Field(default_factory=list)


class SpokenItem(EvalItem):
    task: Annotated[str, Field(min_length=1)]
    task_level_hint: Level
    target_seconds: Annotated[int, Field(ge=30, le=180)] = 60
    transcript: Annotated[str, Field(min_length=1)]
    expected_level: Level
    truth: SpokenTruth


def _overlap(left: list[tuple[int, int]], right: list[tuple[int, int]]) -> bool:
    return any(a < d and c < b for a, b in left for c, d in right)


def _as_errors(rating: SpokenRating) -> list[dict[str, str]]:
    return [
        {"category": e.category, "quote": e.quote, "correction": e.correction}
        for e in rating.language_errors
    ]


def _facets(level: str, weak: set[str], identities: list[str]) -> dict[str, tuple[str, ...]]:
    """What the product uses, each on its own: the level, the weak points, each error."""
    return {
        "level": (level,),
        "weak_points": tuple(sorted(weak)),
        "errors": tuple(sorted(set(identities))),
    }


class PlacementSuite:
    name: str = "placement"
    # v2 (5 November 2026): the same error reported twice is a duplicate, not a false error;
    # stability is also measured on the level, the weak points and each error (facets).
    # v3 (5 November 2026): the spread of the level score between repetitions (measures).
    scorer_version: str = "placement-scorer-v3"
    prompt_roles: tuple[str, ...] = ("placement_speaking",)

    def output_schemas(self, patient_engine: str) -> tuple[type[BaseModel], ...]:
        del patient_engine
        return (SpeakingRatingSchema,)

    def parse_item(self, row: Mapping[str, object]) -> SpokenItem:
        return SpokenItem.model_validate_json(json.dumps(row))

    async def execute(
        self, item: EvalItem, case: MedicalCase, provider: LLMProvider, context: RunContext
    ) -> SpokenRating:
        spoken = cast(SpokenItem, item)
        prompt = context.prompts["placement_speaking"]
        return await rate_spoken_task(
            provider,
            prompt,
            test_language=TEST_LANGUAGE,
            feedback_language=context.feedback_language,
            task=spoken.task,
            task_level_hint=spoken.task_level_hint,
            target_seconds=spoken.target_seconds,
            transcript=spoken.transcript,
            context=ExecutionContext(
                session_id=context.run_key,
                operation="placement_speaking",
                case_version=case.version,
                case_hash=case.content_hash,
                prompt_version=prompt.version,
                prompt_hash=prompt.content_hash,
            ),
            passes=context.evaluation_passes,
        )

    def score(
        self,
        item: EvalItem,
        case: MedicalCase,
        value: object | None,
        calls: Sequence[CallRecord],
    ) -> ItemScore:
        del case, calls
        spoken = cast(SpokenItem, item)
        injected = spoken.truth.errors
        counts: dict[str, float] = {}

        def count(key: str, amount: float = 1.0) -> None:
            counts[key] = counts.get(key, 0.0) + amount

        for error in injected:
            count(f"lang.injected.{error.category}")
        wanted = {p["category"] for p in weak_points([], [e.model_dump() for e in injected])}
        count("level.items")
        if value is None:
            # No usable rating even after a retry: everything annotated was missed.
            for error in injected:
                count(f"lang.fn.{error.category}")
            count("wp.fn.all", len(wanted))
            return ItemScore(
                verdict=False,
                outcomes=(),
                counts=counts,
                signature=json.dumps({"usable": False}),
                facets=_facets("unusable", {"unusable"}, []),
            )
        rating = cast(SpokenRating, value)
        truth_spans = [(e, spans(e.quote, spoken.transcript)) for e in injected]
        unmatched = list(injected)
        outcomes: set[str] = set()
        predicted: list[dict[str, object]] = []
        identities: list[str] = []  # What each report stands for, whatever its quote boundaries.
        for reported in rating.language_errors:
            where = spans(reported.quote, spoken.transcript)
            match = next(
                (
                    e
                    for e, e_where in truth_spans
                    if e in unmatched
                    and e.category == reported.category
                    and _overlap(where, e_where)
                ),
                None,
            )
            already = next(
                (
                    e
                    for e, e_where in truth_spans
                    if e not in unmatched
                    and e.category == reported.category
                    and _overlap(where, e_where)
                ),
                None,
            )
            if match is not None:
                unmatched.remove(match)
                count(f"lang.tp.{reported.category}")
                verdict = "correct"
                identities.append(f"hit:{injected.index(match)}")
            elif already is not None:
                # The same error reported again on other words of its quote ("dich ... dich"):
                # the learner reads one correction twice, not a wrong one. Neither right nor
                # false, counted apart.
                count("lang.duplicate")
                verdict = "duplicate"
                identities.append(f"hit:{injected.index(already)}")
            else:
                count(f"lang.fp.{reported.category}")
                outcomes.add("false_language_error")
                confused = any(_overlap(where, e_where) for _, e_where in truth_spans)
                if confused:
                    count("lang.confused")
                verdict = "wrong_category" if confused else "no_injected_error_there"
                where_wrong = next(
                    (i for i, (_, e_where) in enumerate(truth_spans) if _overlap(where, e_where)),
                    None,
                )
                identities.append(
                    f"{verdict}:{reported.category}:"
                    + (str(where_wrong) if where_wrong is not None else reported.quote)
                )
            count("lang.reported")
            predicted.append(
                {"category": reported.category, "quote": reported.quote, "verdict": verdict}
            )
        for error in unmatched:
            count(f"lang.fn.{error.category}")
            outcomes.add("missed_language_error")

        # The model proposes, the code decides: quotes the code dropped were invented.
        count("quotes.proposed", rating.proposed_errors)
        count("quotes.invented", rating.invented_errors)

        found = {p["category"] for p in weak_points([], _as_errors(rating))}
        count("wp.tp.all", len(found & wanted))
        count("wp.fp.all", len(found - wanted))
        count("wp.fn.all", len(wanted - found))
        if found != wanted:
            outcomes.add("weak_points_mismatch")

        distance = abs(LEVELS.index(rating.estimated_level) - LEVELS.index(spoken.expected_level))
        count("level.exact", float(distance == 0))
        count("level.within_one", float(distance <= 1))
        if distance > 1:
            outcomes.add("level_off")
        signature = {
            "errors": sorted(set(identities)),
            "level": rating.estimated_level,
        }
        return ItemScore(
            verdict=not outcomes,
            outcomes=tuple(sorted(outcomes)),
            counts=counts,
            signature=json.dumps(signature, ensure_ascii=False),
            facets=_facets(rating.estimated_level, found, identities),
            measures={"level_score": rating.level_score},
            detail={
                "estimated_level": rating.estimated_level,
                "level_score": round(rating.level_score, 3),
                "expected_level": spoken.expected_level,
                "confidence": rating.confidence,
                "reported_errors": predicted,
                "missed_errors": [e.model_dump() for e in unmatched],
                "weak_points_found": sorted(found),
                "weak_points_expected": sorted(wanted),
            },
        )

    def ratios(self, keys: Iterable[str]) -> list[Ratio]:
        keys = list(keys)
        categories = sorted(set(suffixes(keys, "lang.injected")))
        every = sorted(
            {*categories, *suffixes(keys, "lang.tp"), *suffixes(keys, "lang.fp")}
            | set(suffixes(keys, "lang.fn"))
        )
        tps = tuple(f"lang.tp.{c}" for c in every)
        fps = tuple(f"lang.fp.{c}" for c in every)
        fns = tuple(f"lang.fn.{c}" for c in every)
        return [
            *(r for c in categories for r in precision_recall("language", "lang", c)),
            Ratio("language_overall.precision", tps, tps + fps),
            Ratio("language_overall.recall", tps, tps + fns),
            Ratio("category_confusion_rate", ("lang.confused",), ("lang.reported",)),
            Ratio("duplicate_rate", ("lang.duplicate",), ("lang.reported",)),
            Ratio("invented_quote_rate", ("quotes.invented",), ("quotes.proposed",)),
            *precision_recall("weak_point", "wp", "all"),
            Ratio("level.within_one", ("level.within_one",), ("level.items",)),
            Ratio("level.exact", ("level.exact",), ("level.items",)),
        ]

    def reliability(self, counts: Counts, producers: Mapping[str, str]) -> list[Reliability]:
        tp = sum(v for k, v in counts.items() if k.startswith("lang.tp."))
        fp = sum(v for k, v in counts.items() if k.startswith("lang.fp."))
        return [
            Reliability(
                source="llm",
                producer=producers["placement_speaking"],
                precision=tp / (tp + fp) if tp + fp else None,
                observations=int(tp + fp),
            )
        ]
