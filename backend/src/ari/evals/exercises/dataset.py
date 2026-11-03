"""`evals/datasets/language-exercises/<version>/`: exercises with a known defect, or none.

Same discipline as the other datasets: every data file is listed with its SHA-256 in the
manifest, a version never changes once published, splits are made of families (the exercises
derived from one base share a family), and the holdout is only read in gate mode.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ari.domain.language_exercises import ExerciseCandidate
from ari.evals.manifest import (
    SPLITS,
    Annotator,
    DatasetError,
    HoldoutAccessError,
    Split,
    sha256_file,
)

SUITE = "language-exercises"
NonEmpty = Annotated[str, Field(min_length=1)]
Defect = Literal[
    "wrong_key",
    "two_correct",
    "no_correct",
    "bad_explanation",
    "wrong_category",
    "level_mismatch",
    "medical_absurd",
    "duplicate_options",
    "bad_format",
]
# Defects a model never sees: the code refuses them before any call.
MECHANICAL_DEFECTS: frozenset[str] = frozenset({"duplicate_options", "bad_format"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Truth(_Strict):
    valid: bool
    defect: Defect | None

    @model_validator(mode="after")
    def consistent(self) -> Truth:
        if self.valid == (self.defect is not None):
            raise ValueError("a valid exercise has no defect, a defective one names it")
        return self


class ExerciseItem(_Strict):
    id: NonEmpty
    family: NonEmpty
    candidate: ExerciseCandidate
    truth: Truth


class ExerciseManifest(_Strict):
    suite: Literal["language-exercises"]
    version: NonEmpty
    description: NonEmpty
    synthetic: Literal[True]
    source: NonEmpty
    created_on: date
    consent: NonEmpty
    annotators: list[Annotator] = Field(min_length=1)
    splits: dict[Split, list[NonEmpty]]
    files: dict[
        Literal["dev.jsonl", "holdout.jsonl"], Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    ]
    limitations: list[NonEmpty] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> ExerciseManifest:
        if set(self.splits) != set(SPLITS):
            raise ValueError("a manifest declares exactly the dev and holdout splits")
        if set(self.splits["dev"]) & set(self.splits["holdout"]):
            raise ValueError("families in both splits")
        if set(self.files) != {"dev.jsonl", "holdout.jsonl"}:
            raise ValueError("the manifest lists dev.jsonl and holdout.jsonl")
        return self


@dataclass(frozen=True, slots=True)
class ExerciseDataset:
    directory: Path
    manifest: ExerciseManifest
    manifest_hash: str

    @property
    def ref(self) -> str:
        return f"{SUITE}@{self.manifest.version}"

    def read_split(self, split: Split, *, gate: bool) -> list[ExerciseItem]:
        if split == "holdout" and not gate:
            raise HoldoutAccessError(
                "Le holdout n'est lu qu'en mode gate (`--gate`) : il ne sert jamais à mettre "
                "au point un prompt."
            )
        families = set(self.manifest.splits[split])
        path = self.directory / f"{split}.jsonl"
        items: list[ExerciseItem] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = ExerciseItem.model_validate_json(json.dumps(json.loads(line)))
            except ValueError as exc:
                raise DatasetError(f"{path.name}:{number}: {exc}") from exc
            if item.family not in families:
                raise DatasetError(
                    f"{path.name}:{number}: family {item.family!r} is not in the {split} split"
                )
            items.append(item)
        if len({i.id for i in items}) != len(items):
            raise DatasetError(f"{path.name}: duplicate item id")
        return items


def load_dataset(root: Path, version: str) -> ExerciseDataset:
    directory = root / SUITE / version
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise DatasetError(f"aucun manifeste pour {SUITE}@{version} dans {directory}")
    manifest = ExerciseManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    for name, expected in manifest.files.items():
        path = directory / name
        if not path.is_file():
            raise DatasetError(f"{SUITE}@{version} : fichier manquant {name}")
        if sha256_file(path) != expected:
            raise DatasetError(
                f"{SUITE}@{version} : {name} ne correspond plus à son empreinte. Une version de "
                "jeu de données ne change jamais : créer une nouvelle version."
            )
    return ExerciseDataset(
        directory, manifest, hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
