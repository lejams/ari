"""Promotion gate of a release (plan, action 8, point 6).

A release needs a passing gate on the real models whenever what the AI does changes: a prompt,
a production model or provider, the routing policy. The AI fingerprint hashes exactly that
surface, from the files of a checkout (no import, so one checkout can fingerprint another):

- every file of `backend/src/ari/prompts/`;
- the prompt versions the production containers load (a switch to an older prompt file);
- the model, provider, effort and engine defaults of `docker-compose.yml`, which production
  runs (a test keeps every AI setting of `Settings` there);
- the routing policy, `infrastructure/providers/llm_routing.py`.

`promote.yml` compares the candidate's fingerprint with the last production release's. When
they differ, it requires a passing verdict of `evals-live.yml` for the same fingerprint: every
run of `PROMOTION_RUNS`, on the holdout, in gate mode, on the real models. A model chosen in the
server's `.env` is outside the repository and outside this gate (docs/EVALS.md).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from ari.evals.report import Report

FINGERPRINT_VERSION = "ai-fingerprint-v1"
VERDICT_VERSION = "promotion-verdict-v1"

PROMPTS_DIR = "backend/src/ari/prompts"
PROMPT_WIRING = ("backend/src/ari/container.py", "backend/src/ari/content/container.py")
POLICY_FILES = ("backend/src/ari/infrastructure/providers/llm_routing.py",)
COMPOSE_FILE = "docker-compose.yml"
COMPOSE_ANCHORS = ("x-provider-env", "x-content-env")
# ARI_PATIENT_MODEL, ARI_EVALUATION_PROVIDER, ARI_ANTHROPIC_EFFORT, ARI_EXAM_ENGINE,
# ARI_PROVIDER_MODE... Voices, keys and timeouts are not the AI's behaviour under test.
AI_VARIABLE = re.compile(r"^ARI_(?:[A-Z0-9_]+_)?(?:MODEL|PROVIDER|EFFORT|ENGINE|PROVIDER_MODE)$")
PROMPT_ID = re.compile(r"\"([a-z][a-z0-9-]*-v\d+)\"")


@dataclass(frozen=True)
class PromotionRun:
    suite: str
    config: str  # File name under evals/configs/.


# What a release must pass. evaluation-medical@1 joins once the doctor's annotations exist;
# voice-sim@1 runs in process on simulated providers (CI) until its staging target exists.
PROMOTION_RUNS: tuple[PromotionRun, ...] = (
    PromotionRun("patient-leakage@1", "luna.yaml"),
    PromotionRun("patient-leakage@1", "luna-exam.yaml"),
    PromotionRun("evaluation-language@2", "luna.yaml"),
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fingerprint(_Model):
    version: str
    sha256: str
    components: dict[str, object]


class RunVerdict(_Model):
    suite: str
    config: str
    status: Literal["passed", "failed", "missing"]
    reason: str | None


class Verdict(_Model):
    version: str
    status: Literal["passed", "failed", "measurement"]
    fingerprint: str
    code: str | None
    runs: list[RunVerdict]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ai_settings(root: Path) -> dict[str, str]:
    """The AI variables of the compose anchors, with the value production gets by default."""
    compose = yaml.safe_load((root / COMPOSE_FILE).read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for anchor in COMPOSE_ANCHORS:
        for name, value in compose[anchor].items():
            if AI_VARIABLE.match(name):
                found[name] = str(value)
    return dict(sorted(found.items()))


def production_prompt_ids(root: Path) -> list[str]:
    ids: set[str] = set()
    for relative in PROMPT_WIRING:
        ids.update(PROMPT_ID.findall((root / relative).read_text(encoding="utf-8")))
    return sorted(ids)


def fingerprint(root: Path) -> Fingerprint:
    prompts_dir = root / PROMPTS_DIR
    components: dict[str, object] = {
        "prompts": {
            path.relative_to(prompts_dir).as_posix(): _sha256(path)
            for path in sorted(prompts_dir.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
        },
        "prompt_ids": production_prompt_ids(root),
        "settings": ai_settings(root),
        "policies": {relative: _sha256(root / relative) for relative in POLICY_FILES},
    }
    canonical = json.dumps(
        {"version": FINGERPRINT_VERSION, "components": components},
        sort_keys=True,
        separators=(",", ":"),
    )
    return Fingerprint(
        version=FINGERPRINT_VERSION,
        sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        components=components,
    )


def _run_verdict(run: PromotionRun, report: Report | None) -> RunVerdict:
    def verdict(status: Literal["passed", "failed", "missing"], reason: str | None) -> RunVerdict:
        return RunVerdict(suite=run.suite, config=run.config, status=status, reason=reason)

    if report is None:
        return verdict("missing", "aucun rapport")
    if report.config.provider != "live":
        return verdict("failed", "pas sur les vrais modèles")
    if report.split != "holdout" or not report.gate_mode:
        return verdict("failed", "pas un run gate sur le holdout")
    if report.gate is None or report.gate.status != "passed":
        return verdict("failed", "gate échoué")
    return verdict("passed", None)


def verdict(
    reports: Sequence[Report],
    config_ids: dict[str, str],
    fingerprint_sha256: str,
    *,
    code: str | None,
    measurement: bool = False,
) -> Verdict:
    """The verdict of a release: every promotion run has a passing gate report.

    `config_ids` maps each configuration file of `PROMOTION_RUNS` to the id it declares. A
    measurement (dev split) is never a passing verdict.
    """
    by_key = {(r.suite, r.config.id): r for r in reports}
    runs = [
        _run_verdict(run, by_key.get((run.suite, config_ids[run.config]))) for run in PROMOTION_RUNS
    ]
    if measurement:
        status: Literal["passed", "failed", "measurement"] = "measurement"
    else:
        status = "passed" if all(r.status == "passed" for r in runs) else "failed"
    return Verdict(
        version=VERDICT_VERSION,
        status=status,
        fingerprint=fingerprint_sha256,
        code=code,
        runs=runs,
    )
