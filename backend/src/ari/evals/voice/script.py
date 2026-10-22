"""Scripts of the simulated learner: `evals/datasets/voice-sim/<version>/`.

`scripts.jsonl` holds one script per published case: the questions in the case's language,
each with the facts it legitimately asks for. `manifest.json` lists the file with its SHA-256,
like the other datasets; a script that changes is a new version. Only invented content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ari.evals.manifest import Annotator, DatasetError, sha256_file

NonEmpty = Annotated[str, Field(min_length=1)]
StepKind = Literal["question", "absent", "off_topic", "foreign"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Step(_Strict):
    id: NonEmpty
    kind: StepKind
    utterance: NonEmpty
    allowed_fact_ids: list[NonEmpty] = Field(default_factory=list)
    expected_fact_ids: list[NonEmpty] = Field(default_factory=list)
    # The learner talks over this answer with the next question (open microphone only).
    interrupt: bool = False
    # The browser confirms playback; False checks that an unconfirmed answer earns no credit.
    acknowledge: bool = True

    @model_validator(mode="after")
    def coherent(self) -> Step:
        if set(self.expected_fact_ids) - set(self.allowed_fact_ids):
            raise ValueError(f"{self.id}: expected facts must be allowed")
        if self.kind != "question" and self.expected_fact_ids:
            raise ValueError(f"{self.id}: only a question expects facts")
        if self.kind in {"off_topic", "foreign"} and self.allowed_fact_ids:
            raise ValueError(f"{self.id}: a refused utterance allows no fact")
        if self.interrupt and not self.acknowledge:
            raise ValueError(f"{self.id}: an interrupted answer is never confirmed anyway")
        return self


class Script(_Strict):
    id: NonEmpty
    case_id: NonEmpty  # A case published by the target (in process: cases/dev).
    language: NonEmpty
    steps: list[Step] = Field(min_length=2)

    @model_validator(mode="after")
    def interruptions_have_a_follow_up(self) -> Script:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.id}: duplicate step id")
        if self.steps[-1].interrupt:
            raise ValueError(f"{self.id}: the last step has no question to interrupt with")
        return self


class VoiceManifest(_Strict):
    suite: Literal["voice-sim"]
    version: NonEmpty
    description: NonEmpty
    synthetic: Literal[True]  # Scripts are invented; a real voice never enters the repository.
    source: NonEmpty
    created_on: date
    annotators: list[Annotator] = Field(min_length=1)
    files: dict[Literal["scripts.jsonl"], Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]]
    limitations: list[NonEmpty] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VoiceDataset:
    directory: Path
    manifest: VoiceManifest
    manifest_hash: str
    scripts: tuple[Script, ...]

    @property
    def ref(self) -> str:
        return f"voice-sim@{self.manifest.version}"


def load_voice_dataset(root: Path, version: str) -> VoiceDataset:
    directory = root / "voice-sim" / version
    path = directory / "manifest.json"
    if not path.is_file():
        raise DatasetError(f"aucun manifeste pour voice-sim@{version} dans {directory}")
    manifest = VoiceManifest.model_validate_json(path.read_text(encoding="utf-8"))
    scripts_path = directory / "scripts.jsonl"
    if sha256_file(scripts_path) != manifest.files["scripts.jsonl"]:
        raise DatasetError(
            f"voice-sim@{version} : scripts.jsonl ne correspond plus à son empreinte. Une version "
            "de jeu de données ne change jamais : créer une nouvelle version."
        )
    scripts = tuple(
        Script.model_validate_json(json.dumps(json.loads(line)))
        for line in scripts_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if len({s.id for s in scripts}) != len(scripts):
        raise DatasetError(f"voice-sim@{version} : identifiant de script en double")
    return VoiceDataset(directory, manifest, sha256_file(path), scripts)
