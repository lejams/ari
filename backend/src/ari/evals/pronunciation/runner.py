"""Speak every sentence with every voice, transcribe it, score it, keep the audio.

The voices are the production adapter (`ElevenLabsStreamingTTSProvider`, one per persona
sound) and the transcriber is the production one (`WhisperTranscriber`, Groq by default), built
from the same settings as the exam chain. Without `--live`, both are simulated: the voice
encodes the text in the signal and the transcriber decodes it, which checks the run, not the
voice.

Output directory:
- `report.json`: every sentence per voice, what was heard, outcome, latency and cost;
- `audio/<voice>/<sentence>.wav` (with `-r<n>` per repetition beyond the first);
- `a-verifier.md`: the sentences to listen to, with what was written and what was heard;
- `ecoute/`: the same audio under anonymous names in a shuffled order, `grille.csv` to rate
  them and `index.html` to play them; `cle-ecoute.json` (outside `ecoute/`) says which is which.

Only invented sentences are spoken; no learner audio is ever recorded here.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import random
import shutil
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ari.application.contracts import AudioStreamEvent, ExecutionContext, TranscriptionConfig
from ari.application.ports.stt import UtteranceTranscriber
from ari.application.ports.tts import StreamingTTSProvider
from ari.application.services.costing import PricingTable, cost_of
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord
from ari.evals.pronunciation.dataset import Snippet, SnippetDataset
from ari.evals.pronunciation.scoring import Outcome, score
from ari.evals.report import format_micro_eur
from ari.evals.voice.simulated import RATE, _execution, encode_speech
from ari.infrastructure.providers.whisper import wav_bytes

REPORT_VERSION = "voice-snippets-report-v1"
LANGUAGE = "de-DE"
OUTCOMES: tuple[Outcome, ...] = ("ok", "listen", "critical_missed", "failed")


@dataclass(frozen=True, slots=True)
class VoiceUnderTest:
    label: str
    voice_id: str
    tts: StreamingTTSProvider
    personas: tuple[str, ...] = ()
    tags: str = ""
    voice_settings: Mapping[str, float] = field(default_factory=dict)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Call(_Model):
    provider: str
    model: str
    status: str
    latency_ms: int
    cost_micro_eur: int | None
    usage: dict[str, Any]


class SentenceResult(_Model):
    voice: str
    snippet_id: str
    category: str
    repetition: int
    text: str
    heard: str | None
    outcome: Outcome
    word_errors: int
    words: int
    critical_terms: int
    missed_terms: list[str]
    audio: str | None
    error: str | None
    tts: Call | None
    stt: Call | None


class VoiceSummary(_Model):
    label: str
    voice_id: str
    personas: list[str]
    tags: str
    voice_settings: dict[str, float]
    sentences: dict[str, int]
    word_error_rate: float | None
    critical_term_recall: float | None
    first_audio_p50_ms: int | None
    first_audio_p95_ms: int | None
    # Synthesis time over audio duration: below 1, the voice streams faster than it plays.
    real_time_factor_p50: float | None
    cost_micro_eur: int | None


class PronunciationReport(_Model):
    report_version: str
    suite: str
    manifest_sha256: str
    created_at: datetime
    code_version: str | None
    live: bool
    language: str
    tts_model: str
    stt_provider: str
    stt_model: str
    pricing_version: str
    duration_ms: int
    voices: list[VoiceSummary]
    results: list[SentenceResult]
    cost_micro_eur: int | None


@dataclass(frozen=True, slots=True)
class PronunciationRun:
    dataset: SnippetDataset
    voices: Sequence[VoiceUnderTest]
    transcriber: UtteranceTranscriber
    pricing: PricingTable
    out_dir: Path
    live: bool
    tts_model: str
    stt_provider: str
    stt_model: str
    repetitions: int = 1
    seed: int | None = None
    code_version: str | None = None


class SimulatedVoice:
    """Speaks the text as a signal that `SimulatedTranscriber` reads back exactly."""

    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        audio = encode_speech(text)
        step = RATE // 5 * 2  # 200 ms of PCM16 per chunk
        for start in range(0, len(audio), step):
            yield AudioStreamEvent(
                "chunk", data=audio[start : start + step], mime_type="audio/pcm;rate=24000"
            )
        yield AudioStreamEvent(
            "completed",
            mime_type="audio/pcm;rate=24000",
            execution=_execution(
                context,
                "text_to_speech",
                {"characters": len(text), "first_audio_ms": 0, "audio_seconds": 0.0},
            ),
        )


async def run_pronunciation(
    request: PronunciationRun, close: Callable[[], Awaitable[None]] | None = None
) -> PronunciationReport:
    """Every voice says every sentence; the voices' connections are closed at the end."""
    try:
        report = await _run(request)
    finally:
        if close is not None:
            await close()
    write_outputs(report, request.out_dir, request.seed)
    return report


async def _run(request: PronunciationRun) -> PronunciationReport:
    started = time.perf_counter()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    config = TranscriptionConfig(
        expected_locales=(LANGUAGE,),
        # No case context: a prompt listing the expected terms would hide a mispronunciation.
        context_prompt="",
        prompt_version=request.dataset.ref,
        prompt_hash=hashlib.sha256(b"").hexdigest(),
    )
    results: list[SentenceResult] = []
    for voice in request.voices:
        for repetition in range(1, request.repetitions + 1):
            for snippet in request.dataset.snippets:
                context = ExecutionContext(
                    session_id=f"voice-snippets:{run_id}",
                    operation="text_to_speech",
                    case_version=request.dataset.ref,
                    case_hash=request.dataset.manifest_hash,
                    turn_id=f"{voice.label}:{snippet.id}:{repetition}",
                )
                results.append(
                    await _sentence(request, voice, snippet, repetition, context, config)
                )
    summaries = [
        _summary(voice, [r for r in results if r.voice == voice.label]) for voice in request.voices
    ]
    costs = [s.cost_micro_eur for s in summaries]
    return PronunciationReport(
        report_version=REPORT_VERSION,
        suite=request.dataset.ref,
        manifest_sha256=request.dataset.manifest_hash,
        created_at=datetime.now(UTC),
        code_version=request.code_version,
        live=request.live,
        language=LANGUAGE,
        tts_model=request.tts_model,
        stt_provider=request.stt_provider,
        stt_model=request.stt_model,
        pricing_version=request.pricing.version,
        duration_ms=int((time.perf_counter() - started) * 1000),
        voices=summaries,
        results=results,
        cost_micro_eur=None if None in costs else sum(c or 0 for c in costs),
    )


async def _sentence(
    request: PronunciationRun,
    voice: VoiceUnderTest,
    snippet: Snippet,
    repetition: int,
    context: ExecutionContext,
    config: TranscriptionConfig,
) -> SentenceResult:
    audio = bytearray()
    tts_record: ExecutionRecord | None = None
    stt_record: ExecutionRecord | None = None
    heard: str | None = None
    error: str | None = None
    try:
        async for event in voice.tts.stream(snippet.text, context):
            if event.type == "chunk" and event.data:
                audio.extend(event.data)
            elif event.type == "completed" and isinstance(event.execution, ExecutionRecord):
                tts_record = event.execution
    except ProviderError as exc:
        tts_record = exc.execution if isinstance(exc.execution, ExecutionRecord) else None
        error = f"voix : {exc.__cause__ or exc}"
    relative: str | None = None
    if audio:
        suffix = f"-r{repetition}" if repetition > 1 else ""
        relative = f"audio/{voice.label}/{snippet.id}{suffix}.wav"
        path = request.out_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav_bytes(bytes(audio), RATE))
        if error is None:
            try:
                transcription = await request.transcriber.transcribe(
                    bytes(audio), sample_rate=RATE, context=context, config=config
                )
                heard, stt_record = transcription.text, transcription.execution
            except ProviderError as exc:
                stt_record = exc.execution if isinstance(exc.execution, ExecutionRecord) else None
                error = f"transcription : {exc.__cause__ or exc}"
    outcome, errors, words, missed = score(snippet.text, snippet.critical, heard)
    return SentenceResult(
        voice=voice.label,
        snippet_id=snippet.id,
        category=snippet.category,
        repetition=repetition,
        text=snippet.text,
        heard=heard,
        outcome=outcome,
        word_errors=errors,
        words=words,
        critical_terms=len(snippet.critical),
        missed_terms=missed,
        audio=relative,
        error=error[:500] if error else None,
        tts=_call(tts_record, request.pricing),
        stt=_call(stt_record, request.pricing),
    )


def _call(record: ExecutionRecord | None, pricing: PricingTable) -> Call | None:
    if record is None:
        return None
    return Call(
        provider=record.provider,
        model=record.model,
        status=str(record.status.value),
        latency_ms=record.latency_ms,
        cost_micro_eur=cost_of(record, pricing).cost_micro_eur,
        usage=dict(record.usage),
    )


def _summary(voice: VoiceUnderTest, results: Sequence[SentenceResult]) -> VoiceSummary:
    words = sum(r.words for r in results)
    terms = sum(r.critical_terms for r in results)
    missed = sum(len(r.missed_terms) for r in results)
    first_audio = [
        int(r.tts.usage["first_audio_ms"])
        for r in results
        if r.tts and isinstance(r.tts.usage.get("first_audio_ms"), int)
    ]
    factors = [
        r.tts.latency_ms / 1000 / float(r.tts.usage["audio_seconds"])
        for r in results
        if r.tts and r.tts.status == "succeeded" and r.tts.usage.get("audio_seconds")
    ]
    calls = [c for r in results for c in (r.tts, r.stt) if c is not None]
    costs = [c.cost_micro_eur for c in calls]
    return VoiceSummary(
        label=voice.label,
        voice_id=voice.voice_id,
        personas=list(voice.personas),
        tags=voice.tags,
        voice_settings=dict(voice.voice_settings),
        sentences={outcome: sum(r.outcome == outcome for r in results) for outcome in OUTCOMES},
        word_error_rate=round(sum(r.word_errors for r in results) / words, 4) if words else None,
        critical_term_recall=round(1 - missed / terms, 4) if terms else None,
        first_audio_p50_ms=_quantile(first_audio, 0.5),
        first_audio_p95_ms=_quantile(first_audio, 0.95),
        real_time_factor_p50=round(sorted(factors)[(len(factors) - 1) // 2], 3)
        if factors
        else None,
        cost_micro_eur=None if None in costs else sum(c or 0 for c in costs),
    )


def _quantile(values: list[int], q: float) -> int | None:
    """Nearest-rank quantile, as in the other reports."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def write_outputs(report: PronunciationReport, out_dir: Path, seed: int | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(report.model_dump_json(indent=2) + "\n", "utf-8")
    (out_dir / "a-verifier.md").write_text(_to_check(report), "utf-8")
    _blind(report, out_dir, seed)


def _to_check(report: PronunciationReport) -> str:
    lines = [f"# {report.suite} : phrases à écouter", ""]
    lines.append(
        "Un terme critique manqué (chiffre, dose, date, médicament, terme médical) n'est pas "
        "forcément une erreur de la voix : le STT peut se tromper. Écouter le fichier."
    )
    for voice in report.voices:
        lines += ["", f"## {voice.label} (`{voice.voice_id}`)", ""]
        flagged = [r for r in report.results if r.voice == voice.label and r.outcome != "ok"]
        if not flagged:
            lines.append("Rien à signaler : chaque phrase a été entendue telle qu'écrite.")
            continue
        for result in sorted(flagged, key=lambda r: OUTCOMES.index(r.outcome), reverse=True):
            lines.append(
                f"- **{result.outcome}** `{result.snippet_id}` ({result.audio or 'sans audio'})"
            )
            lines.append(f"  - écrit : {result.text}")
            lines.append(f"  - entendu : {result.heard if result.heard is not None else '—'}")
            if result.missed_terms:
                lines.append(f"  - termes manqués : {', '.join(result.missed_terms)}")
            if result.error:
                lines.append(f"  - erreur : {result.error}")
    return "\n".join(lines) + "\n"


def _blind(report: PronunciationReport, out_dir: Path, seed: int | None) -> None:
    """Anonymous copies in a shuffled order, a rating sheet, a page to play them."""
    clips = [r for r in report.results if r.audio]
    random.Random(seed).shuffle(clips)
    blind = out_dir / "ecoute"
    blind.mkdir(exist_ok=True)
    key: dict[str, dict[str, object]] = {}
    for number, result in enumerate(clips, start=1):
        name = f"{number:03d}.wav"
        shutil.copyfile(out_dir / str(result.audio), blind / name)
        key[name] = {
            "voice": result.voice,
            "snippet_id": result.snippet_id,
            "repetition": result.repetition,
            "text": result.text,
        }
    (out_dir / "cle-ecoute.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    with (blind / "grille.csv").open("w", newline="", encoding="utf-8") as sheet:
        writer = csv.writer(sheet)
        writer.writerow(
            [
                "fichier",
                "naturel (1-5)",
                "clarté (1-5)",
                "prononciation des chiffres et termes (1-5)",
                "crédible comme patient (1-5)",
                "erreur entendue",
            ]
        )
        for name in key:
            writer.writerow([name, "", "", "", "", ""])
    rows = "\n".join(
        f"<li><span>{html.escape(name)}</span>"
        f'<audio controls preload="none" src="{name}"></audio></li>'
        for name in key
    )
    (blind / "index.html").write_text(_PAGE.replace("{rows}", rows), "utf-8")


_PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Écoute à l'aveugle</title>
<style>
body{font:16px system-ui,sans-serif;margin:16px;max-width:720px}
li{display:flex;gap:12px;align-items:center;margin:6px 0}
audio{flex:1}
</style>
</head><body>
<h1>Écoute à l'aveugle</h1>
<p>Écouter chaque fichier et noter dans <code>grille.csv</code> : naturel, clarté,
prononciation des chiffres et des termes médicaux, crédibilité comme patient (1 à 5).
Une erreur sur un chiffre ou une dose est à écrire dans la dernière colonne.</p>
<ol>
{rows}
</ol>
</body></html>
"""


def summary_text(report: PronunciationReport, out_dir: Path) -> str:
    lines = [
        f"{report.suite} : {len(report.voices)} voix, {len(report.results)} phrases dites, "
        f"{report.duration_ms / 1000:.1f} s, coût {format_micro_eur(report.cost_micro_eur)}"
    ]
    for voice in report.voices:
        counts = ", ".join(f"{name} {count}" for name, count in voice.sentences.items())
        lines.append(f"  {voice.label} ({voice.voice_id}) : {counts}")
        lines.append(
            f"    écart de transcription {_fmt(voice.word_error_rate)}, termes critiques "
            f"entendus {_fmt(voice.critical_term_recall)}, premier audio P50/P95 "
            f"{voice.first_audio_p50_ms}/{voice.first_audio_p95_ms} ms"
        )
        # Why sentences failed, in the log itself: a run that fails everywhere says why.
        errors = Counter(r.error for r in report.results if r.voice == voice.label and r.error)
        for error, count in errors.most_common():
            lines.append(f"    {count} x {error}")
    lines.append(f"Résultats : {out_dir}")
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "non mesuré" if value is None else f"{value:.3f}"
