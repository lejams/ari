"""Dataset manifests: what a dataset version contains, where it comes from, how it is split.

Layout: `<datasets root>/<suite>/<version>/manifest.json`, `cases.json`, `<split>.jsonl`.
Every data file is listed with its SHA-256; a file that no longer matches its hash stops
the run, because a dataset version never changes once published (a change is a new version).
Splits are made of case families, never of random lines, so near-duplicates of a holdout
item cannot sit in the development split. The holdout is only read in gate mode.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ari.evals.cases import EvalCase, EvalCaseFile

Split = Literal["dev", "holdout"]
SPLITS: tuple[Split, ...] = ("dev", "holdout")
NonEmpty = Annotated[str, Field(min_length=1)]


class DatasetError(Exception):
    """The dataset is missing, malformed or does not match its manifest."""


class HoldoutAccessError(DatasetError):
    """The holdout split was requested outside gate mode."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Annotator(_Strict):
    id: NonEmpty
    role: NonEmpty
    annotated_on: date


class Manifest(_Strict):
    suite: NonEmpty
    version: NonEmpty
    description: NonEmpty
    # True for invented content only. Datasets committed to the repository must be synthetic;
    # real learner transcripts live outside it (see docs/EVALS.md) and need recorded consent.
    synthetic: bool
    source: NonEmpty
    created_on: date
    consent: NonEmpty
    annotators: list[Annotator] = Field(min_length=1)
    splits: dict[Split, list[NonEmpty]]
    files: dict[NonEmpty, Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]]
    limitations: list[NonEmpty] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> Manifest:
        if set(self.splits) != set(SPLITS):
            raise ValueError("a manifest declares exactly the dev and holdout splits")
        dev, holdout = (set(self.splits[s]) for s in SPLITS)
        if dev & holdout:
            raise ValueError(f"families in both splits: {sorted(dev & holdout)}")
        expected = {"cases.json", *(f"{s}.jsonl" for s in SPLITS)}
        if set(self.files) != expected:
            raise ValueError(f"the manifest must list exactly {sorted(expected)}")
        if not self.synthetic and self.consent.startswith("not_applicable"):
            raise ValueError("a dataset built from real transcripts needs a recorded consent")
        return self


@dataclass(frozen=True, slots=True)
class Dataset:
    directory: Path
    manifest: Manifest
    manifest_hash: str
    cases: dict[str, EvalCase]

    @property
    def ref(self) -> str:
        return f"{self.manifest.suite}@{self.manifest.version}"

    def read_split(self, split: Split, *, gate: bool) -> list[dict[str, object]]:
        """Raw rows of one split; the holdout is refused unless the caller is a gate run."""
        if split == "holdout" and not gate:
            raise HoldoutAccessError(
                "Le holdout n'est lu qu'en mode gate (`--gate`) : il ne sert jamais à mettre "
                "au point un prompt."
            )
        families = set(self.manifest.splits[split])
        rows: list[dict[str, object]] = []
        path = self.directory / f"{split}.jsonl"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise DatasetError(f"{path.name}:{number}: a row is a JSON object")
            if row.get("family") not in families:
                raise DatasetError(
                    f"{path.name}:{number}: family {row.get('family')!r} is not in the "
                    f"{split} split of the manifest"
                )
            rows.append(row)
        ids = [str(row.get("id")) for row in rows]
        if len(ids) != len(set(ids)):
            raise DatasetError(f"{path.name}: duplicate item id")
        return rows


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_suite_ref(ref: str) -> tuple[str, str]:
    suite, separator, version = ref.partition("@")
    if not separator or not suite or not version:
        raise DatasetError(f"suite attendue sous la forme <nom>@<version>, reçu {ref!r}")
    return suite, version


def load_dataset(root: Path, suite: str, version: str) -> Dataset:
    directory = root / suite / version
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise DatasetError(f"aucun manifeste pour {suite}@{version} dans {directory}")
    manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    if (manifest.suite, manifest.version) != (suite, version):
        raise DatasetError(f"{manifest_path} décrit {manifest.suite}@{manifest.version}")
    for name, expected in manifest.files.items():
        path = directory / name
        if not path.is_file():
            raise DatasetError(f"{suite}@{version} : fichier manquant {name}")
        if sha256_file(path) != expected:
            raise DatasetError(
                f"{suite}@{version} : {name} ne correspond plus à son empreinte. Une version de "
                "jeu de données ne change jamais : créer une nouvelle version."
            )
    case_file = EvalCaseFile.model_validate_json((directory / "cases.json").read_text("utf-8"))
    return Dataset(
        directory=directory,
        manifest=manifest,
        manifest_hash=sha256_file(manifest_path),
        cases={case.id: case for case in case_file.cases},
    )
