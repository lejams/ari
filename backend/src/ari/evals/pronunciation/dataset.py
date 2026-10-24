"""Patient sentences to speak: `evals/datasets/voice-snippets/<version>/`.

`snippets.jsonl` holds one sentence per line, in German, with the terms whose pronunciation
matters (numbers, doses, dates, drug names, medical terms). Each critical term lists the ways
a transcript may write it; a term is heard when any of them is. `manifest.json` lists the file
with its SHA-256, like the other datasets: a sentence that changes is a new version.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ari.evals.manifest import Annotator, DatasetError, sha256_file

NonEmpty = Annotated[str, Field(min_length=1)]
SUITE = "voice-snippets"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Snippet(_Strict):
    id: NonEmpty
    category: NonEmpty
    source: NonEmpty
    text: NonEmpty
    # One entry per critical term, each a list of accepted spellings.
    critical: list[Annotated[list[NonEmpty], Field(min_length=1)]] = Field(default_factory=list)


class SnippetManifest(_Strict):
    suite: Literal["voice-snippets"]
    version: NonEmpty
    description: NonEmpty
    synthetic: Literal[True]  # Invented sentences only; a learner's voice never enters here.
    source: NonEmpty
    created_on: date
    annotators: list[Annotator] = Field(min_length=1)
    files: dict[Literal["snippets.jsonl"], Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]]
    limitations: list[NonEmpty] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SnippetDataset:
    manifest: SnippetManifest
    manifest_hash: str
    snippets: tuple[Snippet, ...]

    @property
    def ref(self) -> str:
        return f"{SUITE}@{self.manifest.version}"


def load_snippets(root: Path, version: str) -> SnippetDataset:
    directory = root / SUITE / version
    path = directory / "manifest.json"
    if not path.is_file():
        raise DatasetError(f"aucun manifeste pour {SUITE}@{version} dans {directory}")
    manifest = SnippetManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if manifest.version != version:
        raise DatasetError(f"{path} décrit {SUITE}@{manifest.version}")
    snippets_path = directory / "snippets.jsonl"
    if not snippets_path.is_file():
        raise DatasetError(f"{SUITE}@{version} : fichier manquant snippets.jsonl")
    if sha256_file(snippets_path) != manifest.files["snippets.jsonl"]:
        raise DatasetError(
            f"{SUITE}@{version} : snippets.jsonl ne correspond plus à son empreinte. Une version "
            "de jeu de données ne change jamais : créer une nouvelle version."
        )
    snippets = tuple(
        Snippet.model_validate_json(json.dumps(json.loads(line)))
        for line in snippets_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if len({s.id for s in snippets}) != len(snippets):
        raise DatasetError(f"{SUITE}@{version} : identifiant de phrase en double")
    return SnippetDataset(manifest, sha256_file(path), snippets)
