"""Why the checks removed the exercises they removed, for reading a dev run in a log.

The report keeps, for the dev split, each exercise's truth, the stage that decided and what the
blind solver and the judge said. A false rejection (a valid exercise removed) costs a generation,
not a learner, but a rate above the threshold needs a cause before any prompt is touched: this
tells whether the solver, the judge (and which criterion) or a mechanical check is too strict.
The holdout keeps its content out of reports and has nothing to show.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from ari.evals.exercises.dataset import ExerciseItem
from ari.evals.report import Report

SHOWN = 40


def rejection_reasons(detail: dict[str, Any]) -> list[str]:
    """The reasons the checks removed one exercise, each a short code."""
    stage = detail.get("stage")
    issues: list[str] = list(detail.get("issues") or [])
    # A refusal of a later pass sees the options in another order: its indices are not those of
    # the dataset, so only the criteria (the judge) or the stage (the solver) can be told.
    later = [i for i in issues if i.startswith("pass_")]
    issues = [i for i in issues if not i.startswith("pass_")]
    if later:
        if stage == "rubric_judge":
            return [f"{later[0]}:judge:{issue}" for issue in issues] or [f"{later[0]}:judge"]
        return [f"{later[0]}:solver"]
    if stage == "mechanical":
        return [f"mechanical:{issue}" for issue in issues] or ["mechanical"]
    if stage == "rubric_judge":
        return [f"judge:{issue}" for issue in issues] or ["judge"]
    if stage == "blind_solver":
        if issues:
            return [f"solver:{issue}" for issue in issues]
        solver = detail.get("solver") or {}
        key = detail.get("key_index")
        if key is None:  # A report from before the key was recorded, and no dataset to read it in.
            return ["solver:cause_unknown_without_the_key"]
        reasons = []
        if solver.get("chosen_index") == -1:
            reasons.append("solver:found_no_correct_option")
        elif solver.get("chosen_index") != key:
            reasons.append("solver:chose_another_option")
        elif solver.get("acceptable_indices") != [key]:
            reasons.append("solver:sees_several_acceptable_options")
        if solver.get("medically_plausible") is False:
            reasons.append("solver:medically_implausible")
        return reasons or ["solver"]
    return [str(stage)] if stage else ["unusable"]


def _explanation(detail: dict[str, Any]) -> str:
    judge = detail.get("judge") or {}
    failing = [
        f"{v['criterion']} : {str(v['reason'])[:160]}"
        for v in judge.get("verdicts", [])
        if not v.get("passed")
    ]
    if "level_fit" in (
        detail.get("issues") or []
    ):  # The level is ruled by the code, not a verdict.
        failing.append(
            f"level_fit : niveau lu {judge.get('estimated_level')} pour {detail.get('level')}"
        )
    if failing:
        return " | ".join(failing)
    return str((detail.get("solver") or {}).get("comment", ""))[:200]


def exercise_diagnosis_text(report: Report, items: Mapping[str, ExerciseItem] | None = None) -> str:
    """`items` (the dataset's dev items by id) completes a report written before the key and the
    stem were recorded in it."""
    if report.gate_mode or report.split != "dev":
        return "Diagnostic : réservé au split dev ; le holdout garde son contenu hors des rapports."
    counts: Counter[str] = Counter()
    stages: Counter[str] = Counter()
    lines: list[str] = []
    valid = rejected = 0
    unusable: Counter[str] = Counter()
    for item in report.items:
        known = items.get(item.item_id) if items else None
        for run in item.runs:
            detail = dict(run.detail or {})
            if known is not None:
                detail.setdefault("key_index", known.candidate.answer_index)
                detail.setdefault("stem", known.candidate.stem)
            if not run.usable:  # No answer even after a retry: neither accepted nor refused.
                unusable[(run.errors[-1][:160] if run.errors else "sans détail")] += 1
                continue
            truth = detail.get("truth") or {}
            if not truth.get("valid"):
                if detail.get("accepted"):
                    lines.append(
                        f"- FAUSSE ACCEPTATION {item.item_id} ({truth.get('defect')}) : "
                        f"{str(detail.get('stem', ''))[:90]}"
                    )
                continue
            valid += 1
            if detail.get("accepted"):
                continue
            rejected += 1
            reasons = rejection_reasons(detail)
            stages[str(detail.get("stage"))] += 1
            counts.update(reasons)
            if rejected <= SHOWN:
                lines.append(
                    f"- {item.item_id} ({item.family}) [{', '.join(reasons)}] "
                    f"« {str(detail.get('stem', ''))[:90]} » : {_explanation(detail)}"
                )
    head = [
        f"Diagnostic de {report.suite} ({report.split}) : "
        f"{rejected} exercice(s) juste(s) refusé(s) sur {valid}",
        "Par étape : " + (", ".join(f"{s} {n}" for s, n in stages.most_common()) or "aucune"),
        "Par raison : " + (", ".join(f"{r} {n}" for r, n in counts.most_common()) or "aucune"),
    ]
    if unusable:
        causes = " | ".join(f"{n} x {text}" for text, n in unusable.most_common(3))
        head.append(
            "Passages sans réponse exploitable (hors décompte) : "
            f"{sum(unusable.values())}, {causes}"
        )
    if rejected > SHOWN:
        lines.append(f"… {rejected - SHOWN} autre(s) refus non détaillé(s)")
    return "\n".join([*head, *lines])
