"""Weekly programme, program-rules-v1: a policy recomputed on every read, never a stored plan.

The week runs Monday to Sunday. Slots are derived from the learner model; volume is
bounded by the declared minutes per day; recommendations score published content by
overlap with due lexicon words, weak anamnesis sections and recency. Done slots are
matched greedily against what was actually completed since Monday.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ari.application.services.learner_model import LearnerModel
from ari.application.services.practice import content_summary
from ari.domain.models import MedicalCase
from ari.domain.placement import LEVELS, level_index
from ari.domain.practice import PracticeContent
from ari.domain.text import normalize_answer

PROGRAM_RULES_VERSION = "program-rules-v1"
EXAM_PHASE_WEEKS = 8
RECENCY_DAYS = 14

# Minutes per slot kind: voice slots add the feedback reading time to the case duration.
LEXICON_MINUTES = 8
FACHBEGRIFFE_MINUTES = 10
ARZT_ARZT_MINUTES = 15
FEEDBACK_MINUTES = 10
PLACEMENT_MINUTES = 15

PHASE_MESSAGES = {
    "positionnement": (
        "Commencez par le test de niveau : sans niveau estimé, ARI ne peut pas calibrer "
        "vos sessions."
    ),
    "prerequis": (
        "Votre niveau estimé est en dessous de B1. ARI travaille le vocabulaire médical et "
        "l'écoute en attendant que la consultation complète soit accessible ; pour l'allemand "
        "général, un cours dédié reste nécessaire. À titre d'information, les Länder exigent en "
        "général un certificat B2 d'allemand général pour s'inscrire à la FSP, à vérifier "
        "auprès de votre Ärztekammer."
    ),
    "fondations": (
        "Niveau B1 : deux consultations d'entraînement par semaine sur des cas accessibles, "
        "le carnet chaque jour et les Fachbegriffe pour élargir le vocabulaire."
    ),
    "anamnese": (
        "Vous tenez une anamnèse : entraînez la structure complète et les moments sensibles, "
        "avec une session en conditions d'examen pour mesurer l'écart."
    ),
    "examen": (
        "Moins de huit semaines avant l'examen : une session d'examen par semaine, deux "
        "entraînements ciblés sur vos sections faibles, et l'Arzt-Arzt pour la présentation."
    ),
}


@dataclass(frozen=True, slots=True)
class Slot:
    id: str
    day_offset: int
    kind: str  # lexicon_review | fachbegriffe | arzt_arzt | voice_training | voice_exam | placement
    minutes: int
    priority: int  # lower is kept first when the budget is tight
    rationale: str
    recommended: dict[str, Any] | None = None


def phase_for(model: LearnerModel) -> str:
    level = model.level_reference
    if level is None:
        return "positionnement"
    if level_index(level) < level_index("B1"):
        return "prerequis"
    if level == "B1":
        return "fondations"
    if model.weeks_left is not None and model.weeks_left <= EXAM_PHASE_WEEKS:
        return "examen"
    return "anamnese"


def week_start(today: date) -> date:
    return today - timedelta(days=today.weekday())


def score_case(model: LearnerModel, case: MedicalCase, today: date) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 0
    overlap = {normalize_answer(t.german) for t in case.terminology} & model.due_lemma_keys
    if overlap:
        score += 2 * len(overlap)
        reasons.append(f"{len(overlap)} mot(s) de votre carnet à revoir dans ce cas")
    weak = {s.id for s in model.weakest_sections[:2] if s.ratio < 1.0}
    if weak & {s.id for s in case.anamnesis_sections}:
        score += 1
        reasons.append("travaille une section d'anamnèse que vous couvrez peu")
    last = model.last_completed_by_scenario.get(case.training_snapshot.get("scenario_id", ""))
    if last is None or (today - last.date()).days >= RECENCY_DAYS:
        score += 1
        reasons.append("pas fait récemment")
    return score, reasons


def compatible(model: LearnerModel, case: MedicalCase) -> bool:
    """A case at most one level above the reference; unknown levels are not filtered."""
    if model.level_reference is None or case.cefr not in LEVELS:
        return True
    return level_index(case.cefr) <= level_index(model.level_reference) + 1


def recommend_case(
    model: LearnerModel, cases: tuple[MedicalCase, ...], today: date
) -> tuple[MedicalCase | None, list[str]]:
    candidates = [c for c in cases if c.available_for_new_sessions]
    filtered = [c for c in candidates if compatible(model, c)] or candidates
    best: tuple[int, int, MedicalCase, list[str]] | None = None
    for index, case in enumerate(filtered):
        score, reasons = score_case(model, case, today)
        if best is None or score > best[0]:
            best = (score, index, case, reasons)
    if best is None:
        return None, []
    return best[2], best[3]


def recommend_practice(
    model: LearnerModel, contents: tuple[PracticeContent, ...], phase: str, today: date
) -> PracticeContent | None:
    matching = [c for c in contents if c.bundle.scenarios[0].phase == phase]
    best: tuple[int, PracticeContent] | None = None
    for content in matching:
        terms = {normalize_answer(t.german) for t in content.bundle.terminology_sets[0].entries}
        score = 2 * len(terms & model.due_lemma_keys)
        last = model.last_completed_by_practice.get(content.scenario_id)
        if last is None or (today - last.date()).days >= RECENCY_DAYS:
            score += 1
        if best is None or score > best[0]:
            best = (score, content)
    return best[1] if best else None


def _case_ref(case: MedicalCase, mode: str) -> dict[str, Any]:
    return {
        "kind": "voice",
        "mode": mode,
        "id": case.id,
        "version": case.version,
        "title": case.title,
        "cefr": case.cefr,
        "training_snapshot": dict(case.training_snapshot),
    }


def _practice_ref(content: PracticeContent) -> dict[str, Any]:
    summary = content_summary(content)
    return {"kind": "practice", "mode": "training", **summary}


def plan_week(
    model: LearnerModel,
    cases: tuple[MedicalCase, ...],
    practice_contents: tuple[PracticeContent, ...],
    today: date,
    *,
    completed: dict[str, list[datetime]],
) -> dict[str, Any]:
    """`completed` maps slot kinds to completion timestamps since the start of the week."""
    phase = phase_for(model)
    start = week_start(today)
    slots: list[Slot] = []

    def add(
        day: int,
        kind: str,
        minutes: int,
        priority: int,
        rationale: str,
        rec: dict[str, Any] | None = None,
    ) -> None:
        slots.append(Slot(f"{kind}-{day}", day, kind, minutes, priority, rationale, rec))

    if phase == "positionnement":
        add(
            0, "placement", PLACEMENT_MINUTES, 0, "Estimer votre niveau pour calibrer le programme."
        )
    if model.lexicon_total:
        for day in range(7):
            add(day, "lexicon_review", LEXICON_MINUTES, 1, "Révision espacée de votre carnet.")

    voice_days = {"fondations": (1, 4), "anamnese": (1, 4), "examen": (1, 4)}.get(phase, ())
    exam_day = (
        6
        if phase == "examen"
        or (phase == "anamnese" and model.weeks_left is not None and model.weeks_left <= 16)
        else None
    )
    case, reasons = recommend_case(model, cases, today)
    if case is not None:
        for day in voice_days:
            add(
                day,
                "voice_training",
                case.educational_target.duration_minutes + FEEDBACK_MINUTES,
                2,
                "Consultation d'entraînement · " + (", ".join(reasons) or "cas disponible"),
                _case_ref(case, "training"),
            )
        if exam_day is not None:
            add(
                exam_day,
                "voice_exam",
                case.educational_target.duration_minutes + FEEDBACK_MINUTES,
                3,
                "Session en conditions d'examen pour mesurer l'écart.",
                _case_ref(case, "exam"),
            )

    fach_days = {
        "positionnement": (2,),
        "prerequis": (0, 2, 4),
        "fondations": (2, 5),
        "anamnese": (3,),
        "examen": (3,),
    }[phase]
    fach = recommend_practice(model, practice_contents, "fachbegriffe", today)
    if fach is not None:
        for day in fach_days:
            add(
                day,
                "fachbegriffe",
                FACHBEGRIFFE_MINUTES,
                4,
                "Vocabulaire médical structuré.",
                _practice_ref(fach),
            )
    if phase in {"anamnese", "examen"}:
        arzt = recommend_practice(model, practice_contents, "arzt_arzt", today)
        if arzt is not None:
            add(
                5,
                "arzt_arzt",
                ARZT_ARZT_MINUTES,
                5,
                "Présentation Arzt-Arzt structurée.",
                _practice_ref(arzt),
            )

    budget = model.minutes_per_day * 7
    kept = sorted(slots, key=lambda s: (s.priority, s.day_offset))
    total = sum(s.minutes for s in kept)
    trimmed: list[str] = []
    while total > budget and kept:
        # Drop the lowest-priority, latest slot first; never drop the placement slot.
        victims = [s for s in kept if s.kind != "placement"]
        if not victims:
            break
        victim = max(victims, key=lambda s: (s.priority, s.day_offset))
        kept.remove(victim)
        trimmed.append(victim.id)
        total -= victim.minutes

    remaining = {kind: sorted(times) for kind, times in completed.items()}
    ordered = sorted(kept, key=lambda s: (s.day_offset, s.priority))
    rendered = []
    for slot in ordered:
        slot_date = start + timedelta(days=slot.day_offset)
        done = bool(remaining.get(slot.kind))
        if done:
            remaining[slot.kind].pop(0)
        state = (
            "done"
            if done
            else "missed"
            if slot_date < today
            else "today"
            if slot_date == today
            else "todo"
        )
        rendered.append(
            {
                "id": slot.id,
                "date": slot_date.isoformat(),
                "day_offset": slot.day_offset,
                "kind": slot.kind,
                "minutes": slot.minutes,
                "state": state,
                "rationale": slot.rationale,
                "recommended": slot.recommended,
            }
        )
    open_slots = [s for s in rendered if s["state"] in {"today", "todo", "missed"}]
    return {
        "version": PROGRAM_RULES_VERSION,
        "phase": phase,
        "message": PHASE_MESSAGES[phase],
        "week_start": start.isoformat(),
        "today": today.isoformat(),
        "budget_minutes": budget,
        "planned_minutes": total,
        "trimmed_slot_ids": trimmed,
        "slots": rendered,
        "next": open_slots[0] if open_slots else None,
        "limitations": (
            "Programme calculé par règles à partir de signaux déterministes (niveau estimé ou "
            "déclaré, carnet, sections couvertes, activité récente). Il se recalcule à chaque "
            "ouverture ; ce n'est pas un plan figé ni une garantie de réussite."
        ),
    }
