"""The gate in front of the bank: an exercise batch is imported only if a passing run backs it.

A batch is accepted for import when a gate report (holdout, `--gate`, real models) passed for
exactly what made the batch: the same solver and judge prompts (by hash), the same checker
model, the same rubric and mechanical-check versions. Change a prompt, a model or the rubric
and the batch is refused until the suite has passed again on that setting.
"""

from __future__ import annotations

from ari.application.exercise_batch import ExerciseBatch
from ari.application.services.exercise_bank import OP_JUDGE, OP_SOLVE
from ari.domain.errors import InvalidStateError
from ari.evals.exercises.dataset import SUITE
from ari.evals.exercises.runner import scorer_version
from ari.evals.report import Report


class GateRefusedError(InvalidStateError):
    pass


def require_passing_gate(report: Report, batch: ExerciseBatch) -> None:
    """Raises `GateRefusedError` with the reason when the report does not back the batch."""

    def refuse(reason: str) -> None:
        raise GateRefusedError(f"Rapport de gate refusé : {reason}")

    if not report.suite.startswith(f"{SUITE}@"):
        refuse(f"c'est un rapport de {report.suite}, pas de {SUITE}")
    if report.config.provider != "live":
        refuse("il ne porte pas sur les vrais modèles")
    if report.split != "holdout" or not report.gate_mode:
        refuse("ce n'est pas un run gate sur le holdout")
    if report.gate is None or report.gate.status != "passed":
        refuse("le gate n'est pas passé")
    expected_scorer = scorer_version(batch.rubric_version, batch.mechanical_checks_version)
    if report.versions.scorer != expected_scorer:
        refuse(
            f"mesuré avec {report.versions.scorer}, le lot a été fait avec {expected_scorer} "
            "(grille ou contrôles mécaniques changés)"
        )
    for role, stage in (("solver", batch.solver), ("judge", batch.judge)):
        measured = report.versions.prompts.get(role)
        if measured is None or measured.sha256 != stage.prompt_hash:
            refuse(f"le prompt {stage.prompt_version} du lot n'est pas celui qui a été mesuré")
    for operation, stage in ((OP_SOLVE, batch.solver), (OP_JUDGE, batch.judge)):
        if stage.model not in report.models_recorded.get(operation, []):
            refuse(
                f"{stage.provider}:{stage.model} ({operation}) n'est pas le modèle qui a été mesuré"
            )
        if report.config.routes.get("checker") != f"{stage.provider}:{stage.model}":
            refuse(f"la route de contrôle mesurée ({report.config.routes.get('checker')}) diffère")
