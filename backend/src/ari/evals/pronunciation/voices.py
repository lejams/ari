"""The voices under test and the transcriber, built like the exam chain builds them."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlparse

from ari.application.ports.stt import UtteranceTranscriber
from ari.config import Settings
from ari.container import exam_persona_voices
from ari.evals.configs import ConfigError
from ari.evals.pronunciation.runner import SimulatedVoice, VoiceUnderTest
from ari.evals.voice.simulated import FAKE_MODEL, SimulatedTranscriber

_LABEL = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


@dataclass(frozen=True, slots=True)
class VoiceSetup:
    voices: tuple[VoiceUnderTest, ...]
    transcriber: UtteranceTranscriber
    tts_model: str
    stt_provider: str
    stt_model: str

    async def close(self) -> None:
        for voice in self.voices:
            close = getattr(voice.tts, "close", None)
            if close is not None:
                await close()


@dataclass(frozen=True, slots=True)
class _Sound:
    label: str
    voice_id: str
    personas: tuple[str, ...]
    tags: str
    voice_settings: dict[str, float]


def parse_candidate(value: str) -> tuple[str, str]:
    label, separator, voice_id = value.partition("=")
    label, voice_id = label.strip(), voice_id.strip()
    if not separator or not voice_id or not _LABEL.match(label):
        raise ConfigError(
            f"--voice attend NOM=VOICE_ID, NOM en lettres, chiffres, - ou _ ; reçu {value!r}"
        )
    return label, voice_id


def _sounds(settings: Settings, candidates: Sequence[str], *, personas: bool) -> list[_Sound]:
    sounds: list[_Sound] = []
    if personas or not candidates:
        # Personas that sound the same are one voice under test, as in the exam chain.
        grouped: dict[str, list[str]] = {}
        described: dict[str, dict[str, object]] = {}
        for persona, sound in exam_persona_voices(settings).items():
            key = json.dumps(sound, sort_keys=True)
            grouped.setdefault(key, []).append(persona)
            described[key] = sound
        for key, members in grouped.items():
            sound = described[key]
            if not sound["voice_id"]:
                raise ConfigError(
                    f"aucune voix pour {', '.join(members)} : ARI_ELEVENLABS_VOICE_ID ou "
                    "ARI_ELEVENLABS_EXAM_PERSONAS"
                )
            sounds.append(
                _Sound(
                    "-".join(members),
                    str(sound["voice_id"]),
                    tuple(members),
                    str(sound["tags"]),
                    cast(dict[str, float], sound["voice_settings"]),
                )
            )
    for value in candidates:
        label, voice_id = parse_candidate(value)
        sounds.append(_Sound(label, voice_id, (), "", {}))
    labels = [s.label for s in sounds]
    if len(labels) != len(set(labels)):
        raise ConfigError(f"nom de voix en double : {labels}")
    return sounds


def build_voices(
    settings: Settings, candidates: Sequence[str], *, personas: bool, live: bool
) -> VoiceSetup:
    sounds = _sounds(settings, candidates, personas=personas)
    if not live:
        return VoiceSetup(
            tuple(
                VoiceUnderTest(
                    s.label, s.voice_id, SimulatedVoice(), s.personas, s.tags, s.voice_settings
                )
                for s in sounds
            ),
            SimulatedTranscriber(),
            FAKE_MODEL,
            "fake",
            FAKE_MODEL,
        )
    from ari.infrastructure.providers.elevenlabs import ElevenLabsStreamingTTSProvider
    from ari.infrastructure.providers.whisper import WhisperTranscriber

    missing = [
        name
        for name, value in (
            ("ARI_ELEVENLABS_API_KEY", settings.elevenlabs_api_key),
            ("ARI_STT_API_KEY", settings.stt_api_key),
        )
        if not value
    ]
    if missing:
        raise ConfigError(f"--live demande {' et '.join(missing)} dans l'environnement")
    assert settings.elevenlabs_api_key and settings.stt_api_key
    voices = tuple(
        VoiceUnderTest(
            s.label,
            s.voice_id,
            ElevenLabsStreamingTTSProvider(
                settings.elevenlabs_api_key,
                model=settings.elevenlabs_exam_tts_model,
                voice_id=s.voice_id,
                timeout_seconds=settings.tts_timeout_seconds,
                output_format=settings.elevenlabs_output_format,
                base_url=settings.elevenlabs_base_url,
                voice_registration=settings.elevenlabs_voice_registration,
                voice_settings=s.voice_settings,
                audio_tags=s.tags,
            ),
            s.personas,
            s.tags,
            s.voice_settings,
        )
        for s in sounds
    )
    transcriber = WhisperTranscriber(
        settings.stt_api_key,
        base_url=settings.stt_base_url,
        model=settings.stt_model,
        timeout_seconds=settings.stt_timeout_seconds,
    )
    return VoiceSetup(
        voices,
        transcriber,
        settings.elevenlabs_exam_tts_model,
        urlparse(settings.stt_base_url).hostname or "whisper",
        settings.stt_model,
    )
