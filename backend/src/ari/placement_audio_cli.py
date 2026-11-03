"""Command line of the placement listenings: record their audio once, list it, export it.

    python -m ari.placement_audio_cli generate ari-placement-de 2
    python -m ari.placement_audio_cli list ari-placement-de 2
    python -m ari.placement_audio_cli export ari-placement-de 2 --out-dir var/placement-audio

`generate` reads each listening script of the set, has it spoken by the project's ElevenLabs voice
(the fake tone in fake mode) and stores the audio for the exact set hash, so the test plays the
same recording to everyone without a call to a voice service. An item that already has this voice
is skipped, a changed set (another hash) is recorded again. `export` writes WAV files so the
recordings can be heard before the set is published. Run it where the provider keys are, once per
set version, before or after `placement publish` (the test synthesises on demand while an item
has no recording).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from ari.application.contracts import ExecutionContext
from ari.application.ports.placement import PlacementAudioStore, StoredAudio
from ari.application.ports.tts import StreamingTTSProvider
from ari.config import Settings
from ari.container import placement_audio_voice
from ari.domain.errors import AriError, InvalidStateError
from ari.domain.placement import PlacementSetVersion
from ari.infrastructure.cases.placement_store import PlacementStore
from ari.infrastructure.persistence.platform.engine import create_platform_engine
from ari.infrastructure.persistence.platform.placement_audio import SqlPlacementAudioStore
from ari.infrastructure.providers.whisper import wav_bytes

_RATE = re.compile(r"rate=(\d+)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audio enregistré des écoutes du test de niveau")
    parser.add_argument("--database-url", help="Base locale; aucune migration automatique")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("generate", "Enregistrer l'audio des écoutes manquantes de ce jeu"),
        ("list", "Dire quelles écoutes ont un audio enregistré"),
        ("export", "Écrire les audios en WAV pour les écouter"),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument("set_id")
        command.add_argument("version")
        if name == "export":
            command.add_argument("--out-dir", type=Path, required=True)
    return parser


async def _record(
    tts: StreamingTTSProvider, text: str, context: ExecutionContext, default_rate: int
) -> tuple[bytes, int]:
    chunks: list[bytes] = []
    rate = default_rate
    async for event in tts.stream(text, context):
        if event.type == "chunk" and event.data:
            chunks.append(event.data)
            found = _RATE.search(event.mime_type)
            if found:
                rate = int(found.group(1))
    audio = b"".join(chunks)
    if not audio:
        raise InvalidStateError(f"Aucun audio reçu pour : {text[:40]}")
    return audio, rate


async def record_listenings(
    placement_set: PlacementSetVersion,
    audio_store: PlacementAudioStore,
    tts: StreamingTTSProvider,
    *,
    voice: str,
    model: str,
    default_rate: int,
) -> dict[str, Any]:
    """Record the listenings of the set that this voice has not recorded yet."""
    recorded: list[str] = []
    skipped: list[str] = []
    for item in placement_set.listening_items:
        if audio_store.has(placement_set.content_hash, item.id, voice):
            skipped.append(item.id)
            continue
        context = ExecutionContext(
            session_id=f"placement-audio:{placement_set.content_hash[:12]}",
            operation="placement_audio",
            case_version=placement_set.version,
            case_hash=placement_set.content_hash,
        )
        pcm16, rate = await _record(tts, item.script, context, default_rate)
        written = audio_store.put(
            placement_set.id,
            placement_set.version,
            placement_set.content_hash,
            item.id,
            StoredAudio(pcm16, rate, voice, model),
        )
        (recorded if written else skipped).append(item.id)
    return {
        "jeu": f"{placement_set.id}@{placement_set.version}",
        "hash": placement_set.content_hash,
        "voix": voice,
        "enregistres": recorded,
        "deja_presents": skipped,
    }


def _status(
    placement_set: PlacementSetVersion, audio_store: PlacementAudioStore
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in placement_set.listening_items:
        stored = audio_store.get(placement_set.content_hash, item.id)
        rows.append(
            {
                "ecoute": item.id,
                "niveau": item.level,
                "audio": stored is not None,
                "voix": stored.voice if stored else None,
                "secondes": (
                    round(len(stored.pcm16) / 2 / stored.sample_rate, 1) if stored else None
                ),
            }
        )
    return rows


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings()
        engine = create_platform_engine(args.database_url or settings.database_url)
        placement_set, status = PlacementStore(engine).get(args.set_id, args.version)
        audio_store = SqlPlacementAudioStore(engine)
        if args.command == "generate":
            tts, voice, model = placement_audio_voice(settings)
            report = asyncio.run(
                record_listenings(
                    placement_set,
                    audio_store,
                    tts,
                    voice=voice,
                    model=model,
                    default_rate=settings.audio_sample_rate,
                )
            )
            report["statut_du_jeu"] = status
            report["ecoutes"] = _status(placement_set, audio_store)
        elif args.command == "list":
            report = {
                "jeu": f"{args.set_id}@{args.version}",
                "statut_du_jeu": status,
                "ecoutes": _status(placement_set, audio_store),
            }
        else:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            written = []
            for item in placement_set.listening_items:
                stored = audio_store.get(placement_set.content_hash, item.id)
                if stored is None:
                    continue
                path = args.out_dir / f"{placement_set.id}-{placement_set.version}-{item.id}.wav"
                path.write_bytes(wav_bytes(stored.pcm16, stored.sample_rate))
                written.append(str(path))
            report = {"fichiers": written}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (AriError, ValueError, OSError) as exc:
        print(f"Opération refusée : {exc}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(run())
