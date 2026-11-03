"""Recorded audio of the placement listenings: stored once per set hash, played by the test."""

from __future__ import annotations

import json
import wave
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any

import pytest
from accounts_fixtures import sign_in
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_placement_v2 import _run_choices, german_bundle

from ari.api.app import create_app
from ari.application.contracts import AudioStreamEvent, ExecutionContext
from ari.application.ports.placement import StoredAudio
from ari.container import Container
from ari.demo import publish_placement_content
from ari.infrastructure.cases.placement_store import PlacementStore
from ari.infrastructure.persistence.platform.placement_audio import SqlPlacementAudioStore
from ari.infrastructure.providers.fake import FakeTTSProvider
from ari.placement_audio_cli import record_listenings, run

TONE = b"\x01\x00" * 4800  # 0.2 s of PCM16 at 24 kHz, recognisable in the WAV


class CountingTTS:
    """The voice synthesised on demand: counts what the test asked it to say."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self._inner = FakeTTSProvider()

    def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        self.spoken.append(text)
        return self._inner.stream(text, context)


@pytest.fixture
def v2_container(published_container: Container) -> Container:
    publish_placement_content(
        PlacementStore(published_container.repository.engine), german_bundle()
    )
    return published_container


def _stored(voice: str = "elevenlabs:voice-1", rate: int = 24_000) -> StoredAudio:
    return StoredAudio(TONE, rate, voice, "eleven_v4_turbo")


def _to_listening(client: TestClient) -> dict[str, Any]:
    sign_in(client)
    client.post("/api/learners", json={"target_cefr": "C1", "details": {"declared_level": "B1"}})
    attempt = client.post("/api/placement/attempts", json={"request_id": "r1"}).json()
    attempt = _run_choices(client, attempt, correct=True)
    assert attempt["phase"] == "listening"
    return attempt  # type: ignore[no-any-return]


def _frames(response_content: bytes) -> bytes:
    with wave.open(BytesIO(response_content)) as container:
        return container.readframes(container.getnframes())


def test_store_keeps_the_latest_voice_and_never_overwrites(v2_container: Container) -> None:
    engine = v2_container.repository.engine
    store, audio = SqlPlacementAudioStore(engine), SqlPlacementAudioStore(engine)
    placement_set = german_bundle().sets[0]
    args = (placement_set.id, placement_set.version, placement_set.content_hash, "l-b1-1")
    assert audio.get(placement_set.content_hash, "l-b1-1") is None
    assert store.put(*args, _stored("elevenlabs:one")) is True
    assert store.put(*args, _stored("elevenlabs:one")) is False  # written once
    assert store.has(placement_set.content_hash, "l-b1-1", "elevenlabs:one")
    assert not store.has(placement_set.content_hash, "l-b1-1", "elevenlabs:two")
    assert store.put(*args, _stored("elevenlabs:two")) is True
    latest = audio.get(placement_set.content_hash, "l-b1-1")
    assert latest is not None and latest.voice == "elevenlabs:two" and latest.pcm16 == TONE
    # Another hash (a changed set) never reads this audio.
    assert audio.get("0" * 64, "l-b1-1") is None
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("UPDATE placement_audio SET model = 'x'"))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("DELETE FROM placement_audio"))


def test_the_test_plays_the_recorded_audio_without_calling_the_voice(
    v2_container: Container,
) -> None:
    placement_set = german_bundle().sets[0]
    voice = CountingTTS()
    v2_container.placement._tts = voice
    store = SqlPlacementAudioStore(v2_container.repository.engine)
    with TestClient(create_app(v2_container)) as client:
        attempt = _to_listening(client)
        item = attempt["current_item"]
        store.put(
            placement_set.id,
            placement_set.version,
            placement_set.content_hash,
            item["id"],
            _stored(),
        )
        url = f"/api/placement/attempts/{attempt['id']}/items/{item['id']}/audio"
        response = client.get(url)
        assert response.status_code == 200 and _frames(response.content) == TONE
        assert voice.spoken == []


def test_an_item_without_a_recording_is_still_synthesised_on_demand(
    v2_container: Container,
) -> None:
    voice = CountingTTS()
    v2_container.placement._tts = voice
    with TestClient(create_app(v2_container)) as client:
        attempt = _to_listening(client)
        item = attempt["current_item"]
        response = client.get(f"/api/placement/attempts/{attempt['id']}/items/{item['id']}/audio")
        assert response.status_code == 200 and len(voice.spoken) == 1


def test_a_recording_made_while_the_server_runs_is_played_at_once(v2_container: Container) -> None:
    placement_set = german_bundle().sets[0]
    voice = CountingTTS()
    v2_container.placement._tts = voice
    store = SqlPlacementAudioStore(v2_container.repository.engine)
    with TestClient(create_app(v2_container)) as client:
        attempt = _to_listening(client)
        item = attempt["current_item"]
        url = f"/api/placement/attempts/{attempt['id']}/items/{item['id']}/audio"
        assert _frames(client.get(url).content) != TONE and len(voice.spoken) == 1
        store.put(
            placement_set.id,
            placement_set.version,
            placement_set.content_hash,
            item["id"],
            _stored(),
        )
        assert _frames(client.get(url).content) == TONE  # no restart, no redeployment
        store.put(
            placement_set.id,
            placement_set.version,
            placement_set.content_hash,
            item["id"],
            StoredAudio(b"\x02\x00" * 4800, 24_000, "elevenlabs:newer", "eleven_v4_turbo"),
        )
        assert _frames(client.get(url).content) == b"\x02\x00" * 4800  # the latest voice
        assert len(voice.spoken) == 1


def test_a_recording_at_another_sample_rate_is_not_played(v2_container: Container) -> None:
    placement_set = german_bundle().sets[0]
    voice = CountingTTS()
    v2_container.placement._tts = voice
    store = SqlPlacementAudioStore(v2_container.repository.engine)
    with TestClient(create_app(v2_container)) as client:
        attempt = _to_listening(client)
        item = attempt["current_item"]
        store.put(
            placement_set.id,
            placement_set.version,
            placement_set.content_hash,
            item["id"],
            _stored(rate=16_000),
        )
        response = client.get(f"/api/placement/attempts/{attempt['id']}/items/{item['id']}/audio")
        assert response.status_code == 200 and _frames(response.content) != TONE
        assert len(voice.spoken) == 1


async def test_recording_the_listenings_of_a_set_is_idempotent(v2_container: Container) -> None:
    engine = v2_container.repository.engine
    placement_set = german_bundle().sets[0]
    store = SqlPlacementAudioStore(engine)
    ids = [item.id for item in placement_set.listening_items]
    voice = CountingTTS()
    args = {"voice": "fake:tone", "model": "fake", "default_rate": 24_000}
    first = await record_listenings(placement_set, store, voice, **args)  # type: ignore[arg-type]
    assert first["enregistres"] == ids and first["deja_presents"] == []
    assert voice.spoken == [item.script for item in placement_set.listening_items]
    second = await record_listenings(placement_set, store, voice, **args)  # type: ignore[arg-type]
    assert second["enregistres"] == [] and second["deja_presents"] == ids
    assert len(voice.spoken) == len(ids)  # nothing spoken again
    recorded = store.get(placement_set.content_hash, ids[0])
    assert recorded is not None and recorded.sample_rate == 24_000 and recorded.pcm16


def test_the_command_records_lists_and_exports(
    v2_container: Container,
    database_url: str,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    placement_set = german_bundle().sets[0]
    base = ["--database-url", database_url]
    assert run([*base, "list", placement_set.id, placement_set.version]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["statut_du_jeu"] == "published"
    assert not any(row["audio"] for row in listed["ecoutes"])

    assert run([*base, "generate", placement_set.id, placement_set.version]) == 0
    generated = json.loads(capsys.readouterr().out)
    assert len(generated["enregistres"]) == len(placement_set.listening_items)
    assert all(row["audio"] and row["voix"] == "fake:tone" for row in generated["ecoutes"])

    out = tmp_path / "wav"
    assert (
        run([*base, "export", placement_set.id, placement_set.version, "--out-dir", str(out)]) == 0
    )
    files = sorted(out.glob("*.wav"))
    assert len(files) == len(placement_set.listening_items)
    with wave.open(str(files[0])) as container:
        assert container.getframerate() == 24_000 and container.getnframes() > 0

    assert run([*base, "list", placement_set.id, "99"]) == 2  # an unknown set is refused
