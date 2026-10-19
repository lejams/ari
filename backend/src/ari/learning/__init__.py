"""The learning core: domain-independent mastery estimation from evidence.

Imports nothing from the rest of ARI and nothing but the standard library (enforced by an
import-linter contract and by `tests/learning/test_learning_purity.py`). A domain plugs in by
turning its results into `Observation`s and its taxonomy into a `SkillFramework`.
"""

from ari.learning.clock import Clock, RandomSource
from ari.learning.framework import FrameworkError, SkillFramework
from ari.learning.mastery import MODEL_VERSION, BetaMasteryModel, MasteryModel, MasteryParams
from ari.learning.model import (
    Candidate,
    Constraints,
    Decision,
    MasteryEstimate,
    Observation,
    ScoredCandidate,
    Skill,
    SkillEdge,
)
from ari.learning.replay import UnknownModelVersion, mastery_model, replay

__all__ = [
    "MODEL_VERSION",
    "BetaMasteryModel",
    "Candidate",
    "Clock",
    "Constraints",
    "Decision",
    "FrameworkError",
    "MasteryEstimate",
    "MasteryModel",
    "MasteryParams",
    "Observation",
    "RandomSource",
    "ScoredCandidate",
    "Skill",
    "SkillEdge",
    "SkillFramework",
    "UnknownModelVersion",
    "mastery_model",
    "replay",
]
