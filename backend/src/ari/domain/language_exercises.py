"""Language exercises: the schema of an item of the bank, the mechanical checks, the grading.

An exercise trains German as a language (grammar, vocabulary, register) at B1, B2 or C1. The
medical setting is a backdrop, never a knowledge to master. Every exercise is a multiple
choice with exactly one correct option, so grading is a comparison of indexes: no model grades a
learner, and a free answer that happens to be correct is never marked wrong.

"The LLM proposes, the code decides": the author model only supplies the sentence, the key, the
distractors and a French explanation. The code assembles the options (shuffled with a seed derived
from the content, so the key does not sit in a favourite position), checks everything it can check
without a model (`mechanical_issues`) and computes the content hash that identifies the item.
Nothing here imports a vendor SDK or the persistence layer.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Annotated, Any, Final, Literal, Self

from pydantic import Field, model_validator

from ari.domain.clinical import ClinicalModel
from ari.domain.clinical_privacy import contact_locations
from ari.domain.models import utc_now
from ari.domain.text import normalize_answer

EXERCISE_SCHEMA: Final = "language-exercise-v1"
MECHANICAL_CHECKS_VERSION = "exercise-mechanical-v1"

ExerciseLevel = Literal["B1", "B2", "C1"]
EXERCISE_LEVELS: tuple[str, ...] = ("B1", "B2", "C1")
# The closed list the session evaluator already uses, without `other` (nothing to train on).
ExerciseCategory = Literal["gender", "case", "verb_form", "word_order", "word_choice", "register"]
EXERCISE_CATEGORIES: tuple[str, ...] = (
    "gender",
    "case",
    "verb_form",
    "word_order",
    "word_choice",
    "register",
)
ExerciseSkill = Literal["grammar", "vocabulary", "register"]
ExerciseFormat = Literal["cloze", "correct_sentence", "register_choice"]
EXERCISE_FORMATS: tuple[str, ...] = ("cloze", "correct_sentence", "register_choice")

SKILL_OF_CATEGORY: dict[str, str] = {
    "gender": "grammar",
    "case": "grammar",
    "verb_form": "grammar",
    "word_order": "grammar",
    "word_choice": "vocabulary",
    "register": "register",
}
# Which formats can train which category: a blank cannot test word order, and a register choice
# needs a register to choose between.
ALLOWED_FORMATS: dict[str, tuple[str, ...]] = {
    "gender": ("cloze", "correct_sentence"),
    "case": ("cloze", "correct_sentence"),
    "verb_form": ("cloze", "correct_sentence"),
    "word_order": ("correct_sentence",),
    "word_choice": ("cloze", "correct_sentence"),
    "register": ("cloze", "register_choice"),
}
INSTRUCTION_FR: dict[str, str] = {
    "cloze": "Choisissez ce qui complète correctement la phrase.",
    "correct_sentence": "Cette phrase contient une erreur. Choisissez sa correction.",
    "register_choice": "Choisissez la formulation qui convient à la situation décrite.",
}
BLANK = "___"

# Bounds, in characters.
STEM_LENGTH = (12, 300)
CLOZE_OPTION_LENGTH = (1, 60)
SENTENCE_OPTION_LENGTH = (12, 300)
EXPLANATION_LENGTH = (15, 500)
MIN_SENTENCE_SIMILARITY = 0.5  # Corrections of one sentence stay close to each other.

# Latin letters (with the accents of German and French) and ordinary punctuation, nothing else.
# The soft hyphen (U+00AD) is left out: invisible, it would make equal options look different.
_ALLOWED_CHARACTERS = re.compile(r"[\u0020-\u007E\u00A0-\u00AC\u00AE-\u024F\u2010-\u2027]*")
# Exactly three underscores make the blank; four or five are not a blank with a stray character.
_BLANK = re.compile(r"(?<!_)___(?!_)")
_MARKUP = re.compile(r"[<>{}\[\]\\|]|https?:|www\.", re.IGNORECASE)


class ExerciseCandidate(ClinicalModel):
    """An exercise as proposed, with structure only. `LanguageExercise` is the checked one."""

    schema_version: Literal["language-exercise-v1"] = EXERCISE_SCHEMA
    level: ExerciseLevel
    category: ExerciseCategory
    format: ExerciseFormat
    # One line of French that sets the situation; required for a register choice.
    context_fr: Annotated[str, Field(max_length=300)] | None = None
    stem: str
    options: tuple[str, ...]
    answer_index: int
    explanation_fr: str
    # The medical backdrop in two or three words ("anamnèse", "prise de rendez-vous").
    setting: Annotated[str, Field(min_length=1, max_length=60)]

    @property
    def skill(self) -> str:
        return SKILL_OF_CATEGORY[self.category]

    @property
    def key(self) -> str:
        return self.options[self.answer_index]

    @property
    def instruction_fr(self) -> str:
        return INSTRUCTION_FR[self.format]

    def correct_sentence(self) -> str:
        """The complete correct sentence, whichever the format."""
        if self.format == "cloze":
            return self.stem.replace(BLANK, self.key, 1)
        return self.key

    def dedup_key(self) -> str:
        """Two exercises with the same correct sentence are the same exercise."""
        return hashlib.sha256(normalize_answer(self.correct_sentence()).encode()).hexdigest()


def mechanical_issues(candidate: ExerciseCandidate) -> list[str]:
    """Why the code refuses this exercise, as short codes; empty when it passes.

    Everything here is decidable without a model. What cannot be decided (is it right German,
    is the distractor really wrong) is the business of the blind solver and the judge.
    """
    issues: list[str] = []
    options = candidate.options
    if not 3 <= len(options) <= 4:
        issues.append("option_count")
    if not 0 <= candidate.answer_index < len(options):
        return [*issues, "answer_out_of_range"]  # Nothing else can be read without a key.
    texts = [candidate.stem, candidate.explanation_fr, candidate.context_fr or "", *options]
    if any(not text.strip() for text in texts[:2]) or any(not o.strip() for o in options):
        issues.append("empty_text")
    folded = [normalize_answer(o) for o in options]
    if len(set(folded)) != len(folded):
        issues.append("duplicate_options")
    if candidate.format not in ALLOWED_FORMATS[candidate.category]:
        issues.append("format_not_allowed_for_category")
    option_bounds = CLOZE_OPTION_LENGTH if candidate.format == "cloze" else SENTENCE_OPTION_LENGTH
    if not STEM_LENGTH[0] <= len(candidate.stem.strip()) <= STEM_LENGTH[1]:
        issues.append("stem_length")
    if any(not option_bounds[0] <= len(o.strip()) <= option_bounds[1] for o in options):
        issues.append("option_length")
    if not EXPLANATION_LENGTH[0] <= len(candidate.explanation_fr.strip()) <= EXPLANATION_LENGTH[1]:
        issues.append("explanation_length")
    if candidate.format == "cloze":
        if len(_BLANK.findall(candidate.stem)) != 1 or candidate.stem.count("_") != len(BLANK):
            issues.append("blank_count")
        if any("_" in o for o in options):
            issues.append("blank_in_option")
    else:
        if "_" in candidate.stem or any("_" in o for o in options):
            issues.append("blank_in_sentence")
        if normalize_answer(candidate.key) == normalize_answer(candidate.stem):
            issues.append("key_equals_stem")  # Nothing to correct or to rewrite.
        similar = all(
            SequenceMatcher(None, normalize_answer(o), normalize_answer(candidate.key)).ratio()
            >= MIN_SENTENCE_SIMILARITY
            for o in options
        )
        if candidate.format == "correct_sentence" and not similar:
            issues.append("options_too_different")
    if candidate.format == "register_choice" and not (candidate.context_fr or "").strip():
        issues.append("context_missing")
    if any(not _ALLOWED_CHARACTERS.fullmatch(text) for text in texts):
        issues.append("unexpected_characters")
    if any(_MARKUP.search(text) for text in texts):
        issues.append("markup_or_link")
    if contact_locations(candidate.model_dump(mode="json")):
        issues.append("contact_details")
    return issues


class LanguageExercise(ExerciseCandidate):
    """An exercise that passed the mechanical checks. Immutable; its hash identifies it."""

    @model_validator(mode="after")
    def passes_mechanical_checks(self) -> Self:
        issues = mechanical_issues(self)
        if issues:
            raise ValueError("Exercice refusé par les contrôles mécaniques : " + ", ".join(issues))
        return self

    @property
    def exercise_id(self) -> str:
        return f"lx-{self.content_hash[:20]}"


def assemble(
    *,
    level: str,
    category: str,
    format: str,
    stem: str,
    key: str,
    distractors: list[str],
    explanation_fr: str,
    setting: str,
    context_fr: str | None = None,
) -> ExerciseCandidate:
    """Options from the author's key and distractors, in an order the code draws.

    The seed comes from the content, so the same proposal always gives the same exercise and the
    key does not follow the author's habits (models like to put the answer first or last). A
    proposal that repeats an option stays a candidate with a duplicate: the mechanical checks
    name the problem instead of the assembly hiding it.
    """
    options = [key.strip(), *(d.strip() for d in distractors)]
    seed = hashlib.sha256("\x1f".join([stem.strip(), *options]).encode()).digest()
    random.Random(seed).shuffle(options)
    return ExerciseCandidate.model_validate_json(
        json.dumps(
            {
                "level": level,
                "category": category,
                "format": format,
                "context_fr": (context_fr or "").strip() or None,
                "stem": stem.strip(),
                "options": options,
                "answer_index": options.index(key.strip()),
                "explanation_fr": explanation_fr.strip(),
                "setting": setting.strip(),
            },
            ensure_ascii=False,
        )
    )


def grade(exercise: ExerciseCandidate, chosen_index: int) -> bool:
    """Right or wrong, by index. The only grading there is."""
    if not 0 <= chosen_index < len(exercise.options):
        raise ValueError("chosen_index hors des options")
    return chosen_index == exercise.answer_index


@dataclass(frozen=True, slots=True)
class LanguageDrill:
    """One answer of a learner to one exercise; kept for the language profile, never edited."""

    id: str
    learner_id: str
    request_id: str  # Idempotency: the same request is the same answer.
    exercise_id: str
    exercise_hash: str
    level: str
    category: str
    chosen_index: int
    correct: bool
    answered_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class ExerciseReview:
    """A check an exercise went through. Appended, never edited."""

    reviewer_kind: Literal["blind_solver", "rubric_judge", "human", "learner_report"]
    reviewer_name: str
    decision: Literal["approve", "reject", "report"]
    payload: dict[str, Any]
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class StoredExercise:
    exercise: LanguageExercise
    batch_id: str
    provenance: dict[str, Any]
    status: Literal["active", "withdrawn"]
    created_at: datetime
    withdrawn_reason: str | None = None
