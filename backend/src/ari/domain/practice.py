"""Deterministic practice state and evidence. No providers or persistence."""

import re
import unicodedata
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ari.domain.clinical import (
    ClinicalBundle,
    ClinicalModel,
    Identifier,
    Text,
)

PracticeMode = Literal["training", "exam"]
PracticeStatus = Literal["active", "paused", "completed"]


def normalize_answer(text: str) -> str:
    """Only case, whitespace and terminal punctuation; never remove negation or numbers."""
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    return re.sub(r"\s+", " ", text).rstrip(".!? ")


class PracticeAnswer(ClinicalModel):
    event_id: Identifier
    question_id: Identifier
    text: Annotated[str, Field(min_length=1, max_length=8000, pattern=r"\S")]
    submitted_at: datetime

    @model_validator(mode="after")
    def timezone_required(self) -> Self:
        if self.submitted_at.tzinfo is None:
            raise ValueError("Date de réponse sans fuseau")
        return self


class PracticeContent(ClinicalModel):
    bundle: ClinicalBundle
    scenario_id: Identifier
    scenario_version: Identifier
    provenance: Literal["published", "synthetic_demo"]

    @model_validator(mode="after")
    def single_valid_scenario(self) -> Self:
        if any(
            len(section) != 1
            for section in (
                self.bundle.scenarios,
                self.bundle.cases,
                self.bundle.rubrics,
                self.bundle.terminology_sets,
            )
        ):
            raise ValueError("Un seul scénario/cas/rubrique/lexique épinglé par exercice")
        scenario = self.bundle.scenarios[0]
        if (scenario.id, scenario.version) != (self.scenario_id, self.scenario_version):
            raise ValueError("Référence de scénario incohérente")
        if scenario.practice is None:
            raise ValueError("Scénario sans exercice explicite")
        if self.provenance == "synthetic_demo" and any(
            source.source_type != "synthetic" for source in self.bundle.sources
        ):
            raise ValueError("Une démonstration exige des sources entièrement synthétiques")
        return self


class PracticeEvidence(ClinicalModel):
    question_id: Identifier
    event_id: Identifier
    submitted_text: Text
    accepted_variant: Text | None


class PracticeItemResult(ClinicalModel):
    item_id: Identifier
    dimension: Identifier
    expected_behavior: Text
    state: Literal["observed", "not_matched", "not_answered"]
    weight: float
    evidence: PracticeEvidence | None
    accepted_answers: tuple[Text, ...]
    coaching_fr: Text


class PracticeDimensionResult(ClinicalModel):
    id: Identifier
    label: Text
    score: float | None
    max_score: int
    answered_weight: float
    expected_weight: float
    state: Literal["no_data", "provisional", "evaluated"]


class PracticeFeedback(ClinicalModel):
    schema_version: Literal["practice-feedback-v1"] = "practice-feedback-v1"
    scoring_version: Literal["practice-exact-answer-v1"] = "practice-exact-answer-v1"
    state: Literal["no_data", "provisional", "evaluated"]
    dimensions: tuple[PracticeDimensionResult, ...]
    items: tuple[PracticeItemResult, ...]
    limitations: Text = (
        "Correspondance textuelle exacte avec des variantes rédigées, pas une validation "
        "clinique, une analyse sémantique libre, une note de prononciation ou un niveau CEFR."
    )
    next_step: Text


class PracticeRun(ClinicalModel):
    id: Identifier
    learner_id: Identifier
    request_id: Identifier
    content: PracticeContent
    mode: PracticeMode
    status: PracticeStatus = "active"
    created_at: datetime
    ended_at: datetime | None = None
    answers: tuple[PracticeAnswer, ...] = ()
    feedback: PracticeFeedback | None = None

    @model_validator(mode="after")
    def consistent_state(self) -> Self:
        specification = self.content.bundle.scenarios[0].practice
        if specification is None:
            raise ValueError("Exercice absent")
        if tuple(a.question_id for a in self.answers) != tuple(
            q.id for q in specification.questions[: len(self.answers)]
        ):
            raise ValueError("Les réponses doivent suivre les questions sans doublon")
        if len({a.event_id for a in self.answers}) != len(self.answers):
            raise ValueError("Événement de réponse dupliqué")
        if (self.status == "completed" and (self.feedback is None or self.ended_at is None)) or (
            self.status != "completed" and (self.feedback is not None or self.ended_at is not None)
        ):
            raise ValueError("Feedback et date de fin exigés pour un exercice terminé seulement")
        if self.created_at.tzinfo is None or (self.ended_at and self.ended_at.tzinfo is None):
            raise ValueError("Dates d'exercice sans fuseau")
        if self.feedback is not None and self.feedback != assess_practice(
            self.content, self.answers
        ):
            raise ValueError("Feedback incohérent avec les réponses et le contenu épinglé")
        return self


def assess_practice(
    content: PracticeContent, answers: tuple[PracticeAnswer, ...]
) -> PracticeFeedback:
    bundle = content.bundle
    scenario, rubric = bundle.scenarios[0], bundle.rubrics[0]
    specification = scenario.practice
    if specification is None:
        raise ValueError("Exercice absent")
    if len({a.question_id for a in answers}) != len(answers):
        raise ValueError("Une seule réponse peut compter par question")
    if len({a.event_id for a in answers}) != len(answers):
        raise ValueError("Événement dupliqué")
    if set(a.question_id for a in answers) - {q.id for q in specification.questions}:
        raise ValueError("Réponse à une question inconnue")
    by_question = {a.question_id: a for a in answers}
    items = {i.id: i for i in specification.assessment_items}
    results = []
    for question in specification.questions:
        item = items[question.assessment_item_id]
        answer = by_question.get(question.id)
        accepted = next(
            (
                variant
                for variant in item.accepted_answers
                if answer and normalize_answer(answer.text) == normalize_answer(variant)
            ),
            None,
        )
        results.append(
            PracticeItemResult(
                item_id=item.id,
                dimension=item.dimension,
                expected_behavior=item.expected_behavior,
                state="not_answered"
                if answer is None
                else "observed"
                if accepted
                else "not_matched",
                weight=item.weight,
                evidence=PracticeEvidence(
                    question_id=question.id,
                    event_id=answer.event_id,
                    submitted_text=answer.text,
                    accepted_variant=accepted,
                )
                if answer
                else None,
                accepted_answers=item.accepted_answers,
                coaching_fr=question.coaching_fr,
            )
        )
    incomplete = len(answers) != len(specification.questions)
    dimensions = []
    for dimension in rubric.dimensions:
        criteria = [i for i in results if i.dimension == dimension.id]
        attempted = sum(i.weight for i in criteria if i.state != "not_answered")
        possible = sum(i.weight for i in criteria)
        earned = sum(i.weight for i in criteria if i.state == "observed")
        dimensions.append(
            PracticeDimensionResult(
                id=dimension.id,
                label=dimension.label,
                score=round(dimension.max_score * earned / attempted, 6) if attempted else None,
                max_score=dimension.max_score,
                answered_weight=attempted,
                expected_weight=possible,
                state="no_data"
                if not attempted
                else "provisional"
                if (incomplete or content.provenance == "synthetic_demo")
                else "evaluated",
            )
        )
    priorities = [i.expected_behavior for i in results if i.state != "observed"]
    return PracticeFeedback(
        state="no_data"
        if not answers
        else "provisional"
        if (incomplete or content.provenance == "synthetic_demo")
        else "evaluated",
        dimensions=tuple(dimensions),
        items=tuple(results),
        next_step="À reprendre avec les variantes et les preuves : " + "; ".join(priorities)
        if priorities
        else "Toutes les réponses attendues ont été observées. Refaire sans aide "
        "ou choisir un autre exercice; cela ne valide pas un niveau clinique ou linguistique.",
    )
