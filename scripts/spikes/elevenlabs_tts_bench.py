"""Spike 3a: measure Eleven v4 Turbo for ARI's patient voice, over the Text to Dialogue WebSocket.

Throwaway measurement tool, not production code. It answers, on YOUR network and with YOUR voices:
  * time to first audio, cold (new connection) and warm (connection reused), P50 / P95;
  * total synthesis time against the audio duration (real-time factor);
  * cost per sentence and per 1 000 characters, from the versioned price list (pricing-v1);
  * the audio itself, as WAV files, plus a shuffled "blind" set for the listening test.

eleven_v4_turbo is only served by the Text to Dialogue WebSocket (the classic text-to-speech
WebSocket refuses it), one voice per connection. The protocol below follows ElevenLabs'
documentation as read on 19 Oct 2026; the first real run may show differences, so frames can be
inspected with --debug-frames (audio payloads are never printed).

Run from the repository root, with the key in the environment (never on the command line):

    ELEVENLABS_API_KEY=... .venv/bin/python scripts/spikes/elevenlabs_tts_bench.py \\
        --voice anna=<voice_id> --voice paul=<voice_id>

Dry run without any key against the local fake server:

    .venv/bin/python scripts/spikes/fake_elevenlabs_server.py &
    ELEVENLABS_API_KEY=dummy .venv/bin/python scripts/spikes/elevenlabs_tts_bench.py \\
        --voice demo=voice-1 --base-url ws://127.0.0.1:8765/v1/text-to-dialogue/stream-input
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import random
import sys
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend" / "src"))

from ari.application.services.costing import cost_of_usage  # noqa: E402
from ari.infrastructure.pricing import load_pricing  # noqa: E402

DEFAULT_URL = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
DEFAULT_SENTENCES = Path(__file__).with_name("german_tts_sentences.txt")
PRICING = REPO / "backend" / "src" / "ari" / "pricing" / "pricing-v1.yaml"
FINAL_KEYS = ("isFinal", "is_final", "final")


@dataclass
class Sentence:
    index: int
    category: str
    text: str


@dataclass
class Sample:
    provider: str
    voice: str
    mode: str  # cold | warm | baseline
    sentence: int
    category: str
    repeat: int
    characters: int
    connect_ms: float | None = None
    ttfb_ms: float | None = None  # from sending the text to the first audio chunk
    cold_ttfb_ms: float | None = (
        None  # from opening the connection to the first audio chunk
    )
    total_ms: float | None = None  # from sending the text to the last audio chunk
    audio_seconds: float | None = None
    audio_bytes: int = 0
    end_reason: str = ""  # final | idle | closed | timeout
    error: str | None = None
    cost_micro_eur: int | None = None
    cost_unknown: tuple[str, ...] = ()


def load_sentences(path: Path) -> list[Sentence]:
    sentences: list[Sentence] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        category, _, text = line.partition("] ")
        sentences.append(Sentence(len(sentences), category.lstrip("["), text.strip()))
    return sentences


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, round(pct / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def pcm_rate(output_format: str) -> int | None:
    """Sample rate of a raw PCM output format such as pcm_24000, else None (mp3, ulaw...)."""
    kind, _, rate = output_format.partition("_")
    return int(rate) if kind == "pcm" and rate.isdigit() else None


def write_wav(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)


class ProtocolError(RuntimeError):
    pass


class DialogueSession:
    """One Text to Dialogue WebSocket, one voice."""

    def __init__(
        self, args: argparse.Namespace, api_key: str, voice_name: str, voice_id: str
    ):
        self.args, self.api_key = args, api_key
        self.voice_name, self.voice_id = voice_name, voice_id
        self.ws: ClientConnection | None = None
        self.frames_logged = 0

    async def open(self) -> float:
        query = urlencode(
            {"model_id": self.args.model, "output_format": self.args.output_format}
        )
        started = time.perf_counter()
        try:
            self.ws = await connect(
                f"{self.args.base_url}?{query}",
                additional_headers={"xi-api-key": self.api_key},
                max_size=None,
                open_timeout=self.args.timeout,
            )
        except InvalidStatus as exc:
            body = (
                exc.response.body.decode("utf-8", "replace")
                if exc.response.body
                else ""
            )
            raise ProtocolError(
                f"Connexion refusée (HTTP {exc.response.status_code}) : {body}"
            ) from exc
        voices: list[Any] = (
            [self.voice_id]
            if self.args.register_shape == "ids"
            else [{"voice_id": self.voice_id}]
        )
        await self.ws.send(json.dumps({"voices": voices}))
        return (time.perf_counter() - started) * 1000

    async def drain(self) -> None:
        """Drop frames left over from the previous sentence so they cannot pollute the next."""
        assert self.ws is not None
        while True:
            try:
                await asyncio.wait_for(self.ws.recv(), 0.05)
            except TimeoutError:
                return

    async def speak(self, text: str, opened_at: float | None) -> tuple[Sample, bytes]:
        assert self.ws is not None
        await self.drain()
        sample = Sample(
            "elevenlabs",
            self.voice_name,
            "cold" if opened_at else "warm",
            0,
            "",
            0,
            len(text),
        )
        message = {"inputs": [{"text": text, "voice_id": self.voice_id}], "flush": True}
        sent = time.perf_counter()
        await self.ws.send(json.dumps(message))
        pcm = bytearray()
        first = last = None
        while True:
            wait = self.args.timeout if first is None else self.args.idle_timeout
            try:
                raw = await asyncio.wait_for(self.ws.recv(), wait)
            except TimeoutError:
                sample.end_reason = "timeout" if first is None else "idle"
                break
            except ConnectionClosed:
                sample.end_reason = "closed"
                break
            frame = (
                json.loads(raw)
                if isinstance(raw, str)
                else {"audio": base64.b64encode(raw)}
            )
            self._log(frame)
            if "error" in frame:
                raise ProtocolError(
                    f"Réponse d'erreur du serveur : {self._redact(frame)}"
                )
            audio = frame.get("audio")
            if audio:
                now = time.perf_counter()
                first = first or now
                last = now
                pcm.extend(base64.b64decode(audio))
            if any(frame.get(key) for key in FINAL_KEYS):
                sample.end_reason = "final"
                break
        if first is not None and last is not None:
            sample.ttfb_ms = (first - sent) * 1000
            sample.total_ms = (last - sent) * 1000
            if opened_at is not None:
                sample.cold_ttfb_ms = (first - opened_at) * 1000
        sample.audio_bytes = len(pcm)
        rate = pcm_rate(self.args.output_format)
        sample.audio_seconds = len(pcm) / (rate * 2) if rate else None
        return sample, bytes(pcm)

    @staticmethod
    def _redact(frame: dict[str, Any]) -> dict[str, Any]:
        return {k: ("<audio>" if k == "audio" else v) for k, v in frame.items()}

    def _log(self, frame: dict[str, Any]) -> None:
        if self.frames_logged < self.args.debug_frames:
            self.frames_logged += 1
            print(f"  [frame] {self._redact(frame)}", file=sys.stderr)

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()


async def openai_baseline(
    client: httpx.AsyncClient, key: str, text: str, model: str, voice: str
) -> tuple[Sample, bytes]:
    """The voice in production today (gpt-4o-mini-tts, retired on 6 Jan 2027), for the blind test."""
    sample = Sample("openai", f"{model}/{voice}", "baseline", 0, "", 0, len(text))
    pcm = bytearray()
    sent = time.perf_counter()
    first = last = None
    body = {"model": model, "voice": voice, "input": text, "response_format": "pcm"}
    async with client.stream(
        "POST",
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
    ) as response:
        if response.status_code != 200:
            detail = (await response.aread()).decode("utf-8", "replace")[:300]
            raise ProtocolError(f"OpenAI HTTP {response.status_code} : {detail}")
        async for chunk in response.aiter_bytes():
            now = time.perf_counter()
            first, last = first or now, now
            pcm.extend(chunk)
    if first is not None and last is not None:
        sample.ttfb_ms, sample.total_ms = (first - sent) * 1000, (last - sent) * 1000
    sample.audio_bytes, sample.end_reason = len(pcm), "final"
    sample.audio_seconds = len(pcm) / (24_000 * 2)
    return sample, bytes(pcm)


def price(sample: Sample, model: str, pricing: Any) -> None:
    usage: dict[str, Any] = {"characters": sample.characters}
    if sample.audio_seconds is not None:
        usage["audio_seconds"] = sample.audio_seconds
    provider, name = (
        ("elevenlabs", model) if sample.provider == "elevenlabs" else ("openai", model)
    )
    result = cost_of_usage(provider, name, usage, pricing)
    sample.cost_micro_eur, sample.cost_unknown = (
        result.cost_micro_eur,
        result.unknown_components,
    )


def fmt(value: float | None, unit: str = "ms") -> str:
    return "n/a" if value is None else f"{value:,.0f} {unit}".replace(",", " ")


def summarize(samples: list[Sample]) -> None:
    print("\n=== Synthèse (médiane / P95) ===")
    print(
        f"{'fournisseur/voix':<28}{'mode':<9}{'n':>4}{'erreurs':>8}  {'1er audio':<20}{'total':<20}{'x temps réel':>13}{'€/1000 car.':>13}"
    )
    groups: dict[tuple[str, str, str], list[Sample]] = {}
    for sample in samples:
        groups.setdefault((sample.provider, sample.voice, sample.mode), []).append(
            sample
        )
    for (provider, voice, mode), group in sorted(groups.items()):
        ok = [s for s in group if s.error is None and s.ttfb_ms is not None]
        ttfb = [
            s.cold_ttfb_ms if mode == "cold" and s.cold_ttfb_ms else s.ttfb_ms
            for s in ok
        ]
        totals = [s.total_ms for s in ok if s.total_ms is not None]
        rtf = [
            s.total_ms / (s.audio_seconds * 1000)
            for s in ok
            if s.total_ms and s.audio_seconds
        ]
        chars = sum(s.characters for s in group if s.cost_micro_eur is not None)
        cost = sum(s.cost_micro_eur or 0 for s in group)
        per_1000 = f"{cost / chars * 1000 / 1_000_000:.4f}" if chars else "inconnu"
        print(
            f"{provider + '/' + voice:<28}{mode:<9}{len(group):>4}{len(group) - len(ok):>8}  "
            f"{fmt(percentile(ttfb, 50)) + ' / ' + fmt(percentile(ttfb, 95)):<20}"
            f"{fmt(percentile(totals, 50)) + ' / ' + fmt(percentile(totals, 95)):<20}"
            f"{(f'{percentile(rtf, 50):.2f}' if rtf else 'n/a'):>13}{per_1000:>13}"
        )
    print(
        "\nmode cold : ouverture de la connexion comprise. warm : connexion déjà ouverte."
    )
    print(
        "Les sessions finissent par 'final' ou 'idle' (silence) ; voir end_reason dans results.json."
    )


async def run(args: argparse.Namespace) -> int:
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        print(
            f"Variable d'environnement {args.api_key_env} absente (ne jamais passer la clé en argument)."
        )
        return 2
    voices = [tuple(v.split("=", 1)) for v in args.voice]
    if any(len(v) != 2 for v in voices):
        print("--voice attend nom=voice_id")
        return 2
    sentences = load_sentences(args.sentences)
    pricing = load_pricing(PRICING)
    out = args.out_dir
    samples: list[Sample] = []
    first_audio: dict[tuple[str, int], bytes] = {}
    rate = pcm_rate(args.output_format)

    for name, voice_id in voices:
        print(f"\n--- voix {name} ({voice_id}) ---", flush=True)
        try:
            for repeat in range(args.cold_repeats):
                for sentence in sentences[: args.cold_limit]:
                    session = DialogueSession(args, api_key, name, voice_id)
                    opened = time.perf_counter()
                    connect_ms = await session.open()
                    sample, _ = await session.speak(sentence.text, opened)
                    await session.close()
                    (
                        sample.connect_ms,
                        sample.sentence,
                        sample.category,
                        sample.repeat,
                    ) = (connect_ms, sentence.index, sentence.category, repeat)
                    price(sample, args.model, pricing)
                    samples.append(sample)
            session = DialogueSession(args, api_key, name, voice_id)
            await session.open()
            for repeat in range(args.repeats):
                for sentence in sentences:
                    sample, pcm = await session.speak(sentence.text, None)
                    sample.sentence, sample.category, sample.repeat = (
                        sentence.index,
                        sentence.category,
                        repeat,
                    )
                    price(sample, args.model, pricing)
                    samples.append(sample)
                    if repeat == 0 and pcm:
                        first_audio[(name, sentence.index)] = pcm
                        if rate:
                            write_wav(
                                out
                                / "audio"
                                / name
                                / f"{sentence.index:02d}_{sentence.category}.wav",
                                pcm,
                                rate,
                            )
            await session.close()
        except ProtocolError as exc:
            print(f"ARRÊT pour la voix {name} : {exc}")
            print(
                "Piste : relancer avec --debug-frames 5, ou --register-shape ids, ou vérifier le modèle et la clé."
            )
            samples.append(
                Sample("elevenlabs", name, "warm", -1, "", 0, 0, error=str(exc))
            )

    if args.baseline_openai:
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            print("OPENAI_API_KEY absente : baseline OpenAI ignorée.")
        else:
            async with httpx.AsyncClient(timeout=args.timeout) as client:
                for sentence in sentences:
                    sample, pcm = await openai_baseline(
                        client,
                        key,
                        sentence.text,
                        args.baseline_model,
                        args.baseline_voice,
                    )
                    sample.sentence, sample.category = sentence.index, sentence.category
                    price(sample, args.baseline_model, pricing)
                    samples.append(sample)
                    first_audio[(sample.voice, sentence.index)] = pcm
                    write_wav(
                        out
                        / "audio"
                        / "openai"
                        / f"{sentence.index:02d}_{sentence.category}.wav",
                        pcm,
                        24_000,
                    )

    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(
        json.dumps([asdict(s) for s in samples], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    build_blind_set(out, sentences, first_audio, rate or 24_000, args)
    summarize(samples)
    print(
        f"\nRésultats : {out / 'results.json'}\nÉchantillons audio : {out / 'audio'}\nÉcoute à l'aveugle : {out / 'blind'}"
    )
    return 0 if not any(s.error for s in samples) else 1


def build_blind_set(
    out: Path,
    sentences: list[Sentence],
    audio: dict[tuple[str, int], bytes],
    rate: int,
    args: argparse.Namespace,
) -> None:
    """Shuffled, anonymously named copies: the key file stays with whoever organises the test."""
    chosen: dict[str, Sentence] = {}
    for sentence in sentences:
        chosen.setdefault(sentence.category, sentence)  # one sentence per category
    rng = random.Random(args.seed)
    entries = [
        (voice, idx)
        for (voice, idx) in audio
        if idx in {s.index for s in chosen.values()}
    ]
    rng.shuffle(entries)
    key: dict[str, dict[str, Any]] = {}
    for number, (voice, idx) in enumerate(entries, 1):
        code = f"{number:03d}"
        write_wav(
            out / "blind" / f"{code}.wav",
            audio[(voice, idx)],
            rate if "gpt-" not in voice else 24_000,
        )
        key[code] = {"voice": voice, "sentence": idx, "text": sentences[idx].text}
    (out / "blind_key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--voice", action="append", default=[], help="nom=voice_id, répétable"
    )
    parser.add_argument("--model", default="eleven_v4_turbo")
    parser.add_argument("--output-format", default="pcm_24000")
    parser.add_argument("--base-url", default=DEFAULT_URL)
    parser.add_argument("--api-key-env", default="ELEVENLABS_API_KEY")
    parser.add_argument("--sentences", type=Path, default=DEFAULT_SENTENCES)
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="passes sur toutes les phrases, connexion réutilisée",
    )
    parser.add_argument("--cold-repeats", type=int, default=1)
    parser.add_argument(
        "--cold-limit",
        type=int,
        default=10,
        help="phrases testées avec une connexion neuve",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=1.5,
        help="silence qui clôt une phrase si aucun message final",
    )
    parser.add_argument(
        "--register-shape", choices=("objects", "ids"), default="objects"
    )
    parser.add_argument(
        "--debug-frames",
        type=int,
        default=0,
        help="affiche les N premières trames (sans l'audio)",
    )
    parser.add_argument(
        "--baseline-openai",
        action="store_true",
        help="ajoute la voix OpenAI actuelle (OPENAI_API_KEY)",
    )
    parser.add_argument("--baseline-model", default="gpt-4o-mini-tts")
    parser.add_argument("--baseline-voice", default="alloy")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="défaut : var/spikes/elevenlabs/<date-heure>, un dossier par exécution",
    )
    args = parser.parse_args()
    if args.out_dir is None:
        args.out_dir = REPO / "var" / "spikes" / "elevenlabs" / time.strftime("%Y%m%d-%H%M%S")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
