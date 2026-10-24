"""The suites the runner knows, by name."""

from __future__ import annotations

from ari.evals.scorers import Suite
from ari.evals.scorers.evaluation_language import EvaluationLanguageSuite
from ari.evals.scorers.evaluation_medical import EvaluationMedicalSuite
from ari.evals.scorers.patient_leakage import PatientLeakageSuite

SUITES: dict[str, Suite] = {
    suite.name: suite
    for suite in (PatientLeakageSuite(), EvaluationLanguageSuite(), EvaluationMedicalSuite())
}
