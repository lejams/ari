"""`voice-sim` in process: the real application, a throwaway database, simulated models.

What is real: the FastAPI application and its voice WebSocket, the push-to-talk buffer, the
exam speech segmenter, the orchestrator, the patient services and their guards, persistence
and playback credit. What is simulated: the transcriber (`SimulatedTranscriber`), the LLM
(the deterministic fake) and the voices (`PacedTTS`). Each session is played by a fresh
account, so neither the one-voice-session rule nor the exam quota is touched or bypassed.

The staging target (real network, real models, 20 sessions per configuration) is not here: it
needs a dedicated evaluation account whose sessions are marked and excluded from the learners'
metrics, which is a production change (see docs/EVALS.md).
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal
from uuid import uuid4

import yaml
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.pool import NullPool

from ari.api.app import create_app
from ari.application.services.costing import PricingTable
from ari.application.services.evaluation import EVALUATION_SCHEMA_VERSION
from ari.application.services.exam_session import ExamVoices
from ari.config import PROJECT_ROOT, Settings
from ari.container import Container, build_container
from ari.demo import seed_bundles
from ari.domain.models import ConversationSession, ExecutionRecord
from ari.evals import gates as gate_rules
from ari.evals.configs import ConfigError
from ari.evals.metrics import COMMON_RATIOS, compute, total
from ari.evals.report import (
    REPORT_VERSION,
    ConfigInfo,
    DatasetInfo,
    ItemEntry,
    LatencySummary,
    PromptVersion,
    Report,
    RunEntry,
    Versions,
    summarize_cost,
    summarize_latency,
)
from ari.evals.runner import call_entry, item_entry
from ari.evals.scorers import CallRecord, EvalItem
from ari.evals.voice.driver import Driver, Mode, StepTrace
from ari.evals.voice.scoring import RATIOS, score_session
from ari.evals.voice.script import Script, VoiceDataset
from ari.evals.voice.simulated import PacedTTS, SimulatedTranscriber
from ari.infrastructure.persistence.schema import upgrade_to_head
from ari.infrastructure.providers.email import LogEmailSender

SCORER_VERSION = "voice-sim-scorer-v1"
DEFAULT_BUNDLES = PROJECT_ROOT / "cases" / "dev"
PROMPTS = PROJECT_ROOT / "backend" / "src" / "ari" / "prompts"
_LINK = re.compile(r"#jeton=([A-Za-z0-9_-]+)")
PASSWORD = "voice-sim synthetic password"


class VoiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
    description: Annotated[str, Field(min_length=1)]
    target: Literal["inprocess"]
    modes: list[Mode] = Field(default=["training", "exam"], min_length=1)
    exam_engine: Literal["chain"] = "chain"


def load_voice_config(path: Path) -> tuple[VoiceConfig, str]:
    raw = path.read_bytes()
    try:
        config = VoiceConfig.model_validate(yaml.safe_load(raw))
    except Exception as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return config, hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class VoiceRunRequest:
    dataset: VoiceDataset
    config: VoiceConfig
    config_sha256: str
    database_url: str  # An empty, migrated platform database used only by this run.
    pricing: PricingTable
    pricing_path: Path
    repetitions: int
    bundles: Path = DEFAULT_BUNDLES
    # Pause between two 100 ms chunks of the patient's voice: the window for an interruption.
    tts_chunk_delay: float = 0.03
    # Simulated recognition errors ({"said": "heard"}), to exercise the transcription measure.
    mishear: dict[str, str] | None = None
    gates: gate_rules.Gates | None = None
    gate_mode: bool = False
    baseline: Report | None = None
    code_version: str | None = None


def run_inprocess(request: VoiceRunRequest) -> Report:
    suite_ref = request.dataset.ref
    if request.gate_mode and (request.gates is None or suite_ref not in request.gates.suites):
        raise ConfigError(f"{suite_ref} n'a pas de seuils dans gates.yaml : run gate refusé")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        provider_mode="fake",
        database_url=request.database_url,
        prompt_directory=PROMPTS,
        pricing_path=request.pricing_path,
        exam_engine=request.config.exam_engine,
    )
    voice = PacedTTS(chunk_delay=request.tts_chunk_delay)
    container = build_container(settings)
    container = replace(
        container,
        transcriber=SimulatedTranscriber(request.mishear),
        tts=voice,
        exam_voices=ExamVoices(MappingProxyType({"standard": voice})),
    )
    seed_bundles(container.cases.store, request.bundles)
    published = {case.id: case for case in container.cases.list()}
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    entries: list[ItemEntry] = []
    latencies: dict[str, list[int]] = {}
    prompts: dict[str, PromptVersion] = {}
    played_cases: dict[str, str] = {}
    try:
        with TestClient(create_app(container)) as client:
            for script in request.dataset.scripts:
                case = published.get(script.case_id)
                if case is None:
                    raise ConfigError(f"{script.id} : le cas {script.case_id} n'est pas publié")
                played_cases[case.id] = f"{case.version}:{case.content_hash}"
                for mode in request.config.modes:
                    item = EvalItem(id=f"{script.id}:{mode}", family=mode, case_id=case.id)
                    runs = []
                    for repetition in range(1, request.repetitions + 1):
                        run = _play(client, container, script, mode, case.version, repetition)
                        prompts.update(_prompt_versions(run.session.executions))
                        run_entry, run_latencies = _run_entry(run, repetition, request.pricing)
                        latencies.setdefault(mode, []).extend(run_latencies)
                        runs.append(run_entry)
                    entries.append(item_entry(item, runs, request.repetitions))
    finally:
        container.repository.engine.dispose()

    summed = total(entry.counts for entry in entries)
    ratios = [*RATIOS, *COMMON_RATIOS]
    metrics: dict[str, float | None] = dict(compute(ratios, summed))
    voice_latency: dict[str, LatencySummary] = {}
    for name, values in sorted(latencies.items()):
        summary = _latency_summary(values)
        voice_latency[f"voice.{name}.end_of_speech_to_first_audio"] = summary
        metrics[f"latency.{name}.p50_ms"] = summary.p50_ms
        metrics[f"latency.{name}.p95_ms"] = summary.p95_ms
    segments = {
        family: compute(ratios, total(e.counts for e in entries if e.family == family))
        for family in sorted({e.family for e in entries})
    }
    calls = [call for entry in entries for run in entry.runs for call in run.calls]
    models_recorded: dict[str, list[str]] = {}
    models_returned: dict[str, list[str]] = {}
    for call in calls:
        for seen, model in ((models_recorded, call.model), (models_returned, call.model_returned)):
            names = seen.setdefault(call.operation, [])
            if model is not None and model not in names:
                names.append(model)
    critical: list[str] = []
    gate = None
    if request.gates is not None and suite_ref in request.gates.suites:
        critical = gate_rules.critical_errors(
            {e.item_id: e.outcomes for e in entries},
            request.gates.suites[suite_ref].critical_outcomes,
        )
        if request.gate_mode:
            gate = gate_rules.evaluate(
                request.gates,
                suite_ref,
                metrics,
                critical,
                baseline_critical=request.baseline.critical_errors if request.baseline else None,
                baseline_id=request.baseline.config.id if request.baseline else None,
            )
    return Report(
        report_version=REPORT_VERSION,
        suite=suite_ref,
        split="dev",
        gate_mode=request.gate_mode,
        started_at=started_at,
        duration_ms=int((time.perf_counter() - started) * 1000),
        repetitions=request.repetitions,
        dataset=DatasetInfo(
            ref=suite_ref,
            manifest_sha256=request.dataset.manifest_hash,
            synthetic=request.dataset.manifest.synthetic,
            split="dev",
            items=len(entries),
            families=sorted({e.family for e in entries}),
            files={str(k): v for k, v in request.dataset.manifest.files.items()},
            cases=dict(sorted(played_cases.items())),
        ),
        config=ConfigInfo(
            id=request.config.id,
            description=request.config.description,
            provider="fake",
            routes={},
            prompts={},
            patient_engine="training+exam",
            feedback_language=settings.feedback_language,
            sha256=request.config_sha256,
            voice={
                "target": request.config.target,
                "modes": ",".join(request.config.modes),
                "exam_engine": request.config.exam_engine,
                "stt": "simulated",
                "tts": "simulated-paced",
                "llm": "fake",
                "audio_pacing": "none",
            },
        ),
        versions=Versions(
            prompts=prompts,
            output_schemas={},
            evaluation_schema_version=EVALUATION_SCHEMA_VERSION,
            scorer=SCORER_VERSION,
            gates=request.gates.version if request.gates else None,
            pricing=request.pricing.version,
            code=request.code_version,
        ),
        models_recorded=models_recorded,
        models_returned=models_returned,
        metrics=metrics,
        segments=segments,
        reliability=[],
        critical_errors=critical,
        cost=summarize_cost(calls, sum(len(e.runs) for e in entries), request.pricing.version),
        latency={**summarize_latency(calls), **voice_latency},
        gate=gate,
        items=entries,
    )


@dataclass(frozen=True, slots=True)
class _PlayedSession:
    traces: list[StepTrace]
    session: ConversationSession
    mode: Mode
    duration_ms: int


def _play(
    client: TestClient,
    container: Container,
    script: Script,
    mode: Mode,
    version: str,
    repetition: int,
) -> _PlayedSession:
    _sign_up(client, container, f"voice-sim-{script.id}-{mode}-{repetition}@example.test")
    learner = client.post("/api/learners", json={}).json()
    response = client.post(
        "/api/sessions",
        json={
            "learner_id": learner["id"],
            "case_id": script.case_id,
            "case_version": version,
            "learning_mode": mode,
        },
    )
    if response.status_code != 201:
        raise RuntimeError(f"session refused for {script.id}: {response.text}")
    session_id = response.json()["id"]
    began = time.perf_counter()
    with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
        traces = Driver(socket, mode, vad_silence_ms=container.settings.exam_vad_silence_ms).play(
            script
        )
    ended = client.post(f"/api/sessions/{session_id}/end")
    if ended.status_code != 200:
        raise RuntimeError(f"end of session refused for {script.id}: {ended.text}")
    return _PlayedSession(
        traces=traces,
        session=container.repository.get_session(session_id),
        mode=mode,
        duration_ms=int((time.perf_counter() - began) * 1000),
    )


def _sign_up(client: TestClient, container: Container, email: str) -> None:
    assert isinstance(container.email, LogEmailSender)  # environment=test: nothing is sent.
    assert client.post("/api/auth/signup", json={"email": email}).status_code == 202
    texts = [m.text for m in container.email.sent if m.to == email]
    match = _LINK.search(texts[-1]) if texts else None
    if match is None:
        raise RuntimeError(f"no activation link for {email}")
    response = client.post(
        "/api/auth/password", json={"token": match.group(1), "password": PASSWORD}
    )
    if response.status_code != 200:
        raise RuntimeError(f"activation refused for {email}: {response.text}")


def _run_entry(
    played: _PlayedSession, repetition: int, pricing: PricingTable
) -> tuple[RunEntry, list[int]]:
    session = played.session
    counts, outcomes, detail, signature = score_session(played.traces, session, played.mode)
    counts["runs.total"] = 1.0
    counts["runs.usable"] = 1.0 if "turn_failed" not in outcomes else 0.0
    counts["runs.first_try"] = counts["runs.usable"]
    calls = [call_entry(CallRecord(e, None), pricing) for e in session.executions]
    latencies = [d["latency_ms"] for d in detail if d["latency_ms"] is not None]
    return (
        RunEntry(
            repetition=repetition,
            attempts=1,
            usable=counts["runs.usable"] == 1.0,
            verdict=not outcomes,
            outcomes=outcomes,
            counts=counts,
            signature=signature,
            errors=[e for d in detail for e in d["errors"]],
            calls=calls,
            detail={
                "session_id": session.id,
                "duration_ms": played.duration_ms,
                "turns": len(session.turns),
                "steps": detail,
            },
        ),
        latencies,
    )


def _latency_summary(values: list[int]) -> LatencySummary:
    """Nearest-rank quantiles, as for the calls."""
    ordered = sorted(values)

    def quantile(q: float) -> int | None:
        return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else None

    return LatencySummary(
        calls=len(ordered),
        p50_ms=quantile(0.5),
        p95_ms=quantile(0.95),
        max_ms=ordered[-1] if ordered else None,
    )


def _prompt_versions(executions: tuple[ExecutionRecord, ...]) -> dict[str, PromptVersion]:
    """Prompts used, as recorded on the calls themselves: `<operation>@<prompt version>`."""
    return {
        f"{e.operation}@{e.prompt_version}": PromptVersion(
            version=e.prompt_version, sha256=e.prompt_hash
        )
        for e in executions
        if e.prompt_version and e.prompt_hash and e.operation != "speech_to_text"
    }


@contextmanager
def throwaway_database(admin_url: str) -> Iterator[str]:
    """A new platform database migrated to head, dropped afterwards whatever happens.

    `admin_url` is a maintenance database with a role allowed to create databases, like the
    tests' `ARI_TEST_DATABASE_URL`. Nothing of an existing database is read or written.
    """
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    name = f"ari_voice_sim_{uuid4().hex[:12]}"
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        upgrade_to_head(url, "platform")
        yield url
    finally:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        engine.dispose()
