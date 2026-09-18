"""Practice orchestration and the only learner-facing projection of exercise content."""

from typing import Any

from ari.application.ports.practice import PracticeCatalog, PracticeRepository
from ari.domain.errors import InvalidStateError
from ari.domain.models import new_id, utc_now
from ari.domain.practice import (
    PracticeAnswer,
    PracticeContent,
    PracticeMode,
    PracticeRun,
    assess_practice,
)


def content_summary(content: PracticeContent) -> dict[str, Any]:
    bundle = content.bundle
    scenario, case, rubric, terms = (
        bundle.scenarios[0],
        bundle.cases[0],
        bundle.rubrics[0],
        bundle.terminology_sets[0],
    )
    return {
        "scenario_id": scenario.id,
        "scenario_version": scenario.version,
        "scenario_hash": scenario.content_hash,
        "case_id": case.id,
        "case_version": case.version,
        "case_hash": case.content_hash,
        "rubric_id": rubric.id,
        "rubric_version": rubric.version,
        "rubric_hash": rubric.content_hash,
        "terminology_id": terms.id,
        "terminology_version": terms.version,
        "terminology_hash": terms.content_hash,
        "phase": scenario.phase,
        "title": case.title,
        "summary": case.public_summary,
        "land": case.location.land.value if case.location.land else None,
        "city": case.location.city,
        "provenance": content.provenance,
        "duration_minutes": scenario.duration_minutes,
        "scoring_version": "practice-exact-answer-v1",
        "limitation": "Exercice textuel structuré; "
        "pas de validation médicale ni de niveau certifié.",
    }


def public_practice_run(run: PracticeRun) -> dict[str, Any]:
    """Never serialize the raw pinned bundle at an API boundary (it contains answers)."""
    specification = run.content.bundle.scenarios[0].practice
    if specification is None:
        raise InvalidStateError("Exercice absent")
    case = run.content.bundle.cases[0]
    terms = {t.id: t.german for t in run.content.bundle.terminology_sets[0].entries}
    current = None
    if run.status != "completed" and len(run.answers) < len(specification.questions):
        question = specification.questions[len(run.answers)]
        current = {
            "id": question.id,
            "kind": question.kind,
            "prompt_de": question.prompt_de,
            "term_de": terms.get(question.term_id or ""),
        }
        if run.mode == "training":
            current["coaching_fr"] = question.coaching_fr
    result: dict[str, Any] = {
        "id": run.id,
        "mode": run.mode,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "content": content_summary(run.content),
        "context": [
            {
                "fact_id": f.id,
                "text_de": f.patient_phrases_de[0],
                "value": f.value,
                "unit": f.unit,
                "polarity": f.polarity,
            }
            for f in case.facts
            if f.id in specification.context_fact_ids
        ],
        "question_count": len(specification.questions),
        "current_question": current,
        "answers": [
            {
                "question_id": a.question_id,
                "event_id": a.event_id,
                "text": a.text,
                "prompt_de": specification.questions[index].prompt_de,
            }
            for index, a in enumerate(run.answers)
        ],
        "feedback": run.feedback.model_dump(mode="json")
        if run.status == "completed" and run.feedback
        else None,
    }
    if run.mode == "training" and run.status != "completed" and run.answers:
        result["training_feedback"] = (
            assess_practice(run.content, run.answers)
            .items[len(run.answers) - 1]
            .model_dump(mode="json")
        )
    return result


class PracticeService:
    def __init__(self, catalog: PracticeCatalog, repository: PracticeRepository) -> None:
        self.catalog, self.repository = catalog, repository

    def start(
        self, learner_id: str, scenario_id: str, version: str, mode: PracticeMode, request_id: str
    ) -> PracticeRun:
        existing = self.repository.find_request(learner_id, request_id)
        if existing:
            if (existing.content.scenario_id, existing.content.scenario_version, existing.mode) != (
                scenario_id,
                version,
                mode,
            ):
                raise InvalidStateError("Clé de démarrage réutilisée avec un autre exercice")
            return existing
        content = self.catalog.get(scenario_id, version)
        return self.repository.create(
            PracticeRun(
                id=new_id(),
                learner_id=learner_id,
                request_id=request_id,
                content=content,
                mode=mode,
                created_at=utc_now(),
            )
        )

    def answer(
        self, learner_id: str, run_id: str, question_id: str, text: str, event_id: str
    ) -> PracticeRun:
        return self.repository.append_answer(
            run_id,
            learner_id,
            PracticeAnswer(
                event_id=event_id,
                question_id=question_id,
                text=text,
                submitted_at=utc_now(),
            ),
        )

    def finish(self, learner_id: str, run_id: str) -> PracticeRun:
        run = self.repository.get(run_id, learner_id)
        if run.status == "completed":
            return run
        feedback = assess_practice(run.content, run.answers)
        return self.repository.finish(run_id, learner_id, feedback, len(run.answers))
