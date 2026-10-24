"""`voice-snippets`: fixed sentences spoken by each voice, heard back, scored by code."""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from scripted import DATASETS, PRICING

from ari.application.contracts import AudioStreamEvent, ExecutionContext
from ari.config import Settings
from ari.domain.errors import ProviderError
from ari.evals.__main__ import main
from ari.evals.configs import ConfigError
from ari.evals.manifest import DatasetError
from ari.evals.pronunciation.dataset import load_snippets
from ari.evals.pronunciation.runner import (
    PronunciationReport,
    PronunciationRun,
    SimulatedVoice,
    VoiceUnderTest,
    run_pronunciation,
)
from ari.evals.pronunciation.scoring import german_number, normalize, score
from ari.evals.pronunciation.voices import build_voices, parse_candidate
from ari.evals.voice.simulated import SimulatedTranscriber
from ari.infrastructure.providers.elevenlabs import ElevenLabsStreamingTTSProvider


def settings(**options: Any) -> Settings:
    return Settings(_env_file=None, **options)


@pytest.mark.parametrize(
    ("value", "words"),
    [
        (0, "null"),
        (1, "eins"),
        (21, "einundzwanzig"),
        (34, "vierunddreißig"),
        (100, "einhundert"),
        (102, "einhundertzwei"),
        (150, "einhundertfünfzig"),
        (400, "vierhundert"),
        (1000, "eintausend"),
        (2026, "zweitausendsechsundzwanzig"),
    ],
)
def test_german_numbers(value: int, words: str) -> None:
    assert german_number(value) == words


def test_digits_words_units_and_hyphens_normalize_alike() -> None:
    assert normalize("38,5 Grad") == normalize("achtunddreißig Komma fünf Grad")
    assert normalize("50 mg") == normalize("fünfzig Milligramm")
    assert normalize("38,4°C") == normalize("38,4 Grad")
    assert normalize("Salbutamol-Spray") == normalize("Salbutamolspray") == ["salbutamolspray"]
    assert normalize("Ich bin vierunddreißig.") == normalize("ich bin 34")


def test_outcomes() -> None:
    critical = [["38,5"], ["37,9"]]
    text = "Gestern hatte ich 38,5 Grad Fieber, heute Morgen noch 37,9."
    assert score(text, critical, text)[0] == "ok"
    outcome, errors, _, missed = score(text, critical, "Gestern hatte ich 38,5 Fieber, heute 37,9.")
    assert outcome == "listen" and errors == 3 and missed == []
    outcome, _, _, missed = score(text, critical, "Gestern hatte ich 35 Grad Fieber, heute 37,9.")
    assert outcome == "critical_missed" and missed == ["38,5"]
    assert score(text, critical, None) == ("failed", 14, 14, ["38,5", "37,9"])
    # Any accepted spelling of a term counts as heard.
    assert score("1,76 Meter", [["1,76", "eins sechsundsiebzig"]], "eins sechsundsiebzig Meter")[
        0
    ] == ("listen")


def test_committed_snippets_load_and_every_critical_term_is_in_its_sentence() -> None:
    dataset = load_snippets(DATASETS, "1")
    assert dataset.manifest.synthetic and len(dataset.snippets) >= 30
    for snippet in dataset.snippets:
        assert score(snippet.text, snippet.critical, snippet.text)[0] == "ok", snippet.id


def test_a_changed_snippet_file_is_refused(tmp_path: Path) -> None:
    shutil.copytree(DATASETS / "voice-snippets", tmp_path / "voice-snippets")
    path = tmp_path / "voice-snippets" / "1" / "snippets.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("Ramipril", "Ramiprol"), "utf-8")
    with pytest.raises(DatasetError, match="empreinte"):
        load_snippets(tmp_path, "1")


def test_fake_run_writes_report_audio_and_a_blind_set(tmp_path: Path) -> None:
    out = tmp_path / "run"
    code = main(
        [
            *("pronunciation", "--voice", "anna=v-anna", "--personas", "--seed", "3"),
            *("--out-dir", str(out)),
        ]
    )
    assert code == 0
    report = PronunciationReport.model_validate_json((out / "report.json").read_text("utf-8"))
    snippets = len(load_snippets(DATASETS, "1").snippets)
    assert [v.label for v in report.voices] == ["standard-anxious-talkative-terse", "anna"]
    assert report.voices[0].voice_id == "3nMIMZ7RlGwsq1WLgxY3"
    assert not report.live and report.cost_micro_eur == 0
    for voice in report.voices:
        assert voice.sentences == {"ok": snippets, "listen": 0, "critical_missed": 0, "failed": 0}
        assert voice.critical_term_recall == 1.0 and voice.word_error_rate == 0.0
    assert len(list((out / "audio" / "anna").glob("*.wav"))) == snippets
    blind = sorted(p.name for p in (out / "ecoute").glob("*.wav"))
    assert blind == [f"{n:03d}.wav" for n in range(1, 2 * snippets + 1)]
    key = json.loads((out / "cle-ecoute.json").read_text("utf-8"))
    assert set(key) == set(blind) and {k["voice"] for k in key.values()} == {
        "anna",
        "standard-anxious-talkative-terse",
    }
    assert not (out / "ecoute" / "cle-ecoute.json").exists()  # The key stays out of the set.
    assert (out / "ecoute" / "grille.csv").read_text("utf-8").count("\n") == 2 * snippets + 1
    assert "001.wav" in (out / "ecoute" / "index.html").read_text("utf-8")
    assert "Rien à signaler" in (out / "a-verifier.md").read_text("utf-8")


class _BrokenVoice:
    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        raise ProviderError("ElevenLabs speech synthesis failed", execution=None)
        yield AudioStreamEvent("completed")  # pragma: no cover


async def test_misheard_numbers_and_failed_voices_are_flagged(tmp_path: Path) -> None:
    dataset = load_snippets(DATASETS, "1")
    report = await run_pronunciation(
        PronunciationRun(
            dataset=dataset,
            voices=[
                VoiceUnderTest("good", "v1", SimulatedVoice()),
                VoiceUnderTest("broken", "v2", _BrokenVoice()),
            ],
            transcriber=SimulatedTranscriber(mishear={"38,5": "35", "Tiotropium": "Tiotropin"}),
            pricing=PRICING,
            out_dir=tmp_path,
            live=False,
            tts_model="fake",
            stt_provider="fake",
            stt_model="fake",
        )
    )
    good, broken = report.voices
    assert good.sentences["critical_missed"] == 2 and good.critical_term_recall is not None
    assert good.critical_term_recall < 1
    flagged = {r.snippet_id: r.missed_terms for r in report.results if r.voice == "good"}
    assert flagged["fever-two"] == ["38,5"] and flagged["atem-medication"] == ["tiotropium"]
    assert broken.sentences["failed"] == len(dataset.snippets)
    assert all(r.audio is None and r.error for r in report.results if r.voice == "broken")
    to_check = (tmp_path / "a-verifier.md").read_text("utf-8")
    assert "fever-two" in to_check and "38,5" in to_check


def test_personas_that_sound_the_same_are_one_voice() -> None:
    personas = json.dumps(
        {"anxious": {"voice_id": "v-anx", "tags": "[nervous]", "voice_settings": {"speed": 1.1}}}
    )
    setup = build_voices(
        settings(elevenlabs_exam_personas=personas), [], personas=False, live=False
    )
    labels = {v.label: v for v in setup.voices}
    assert set(labels) == {"standard-talkative-terse", "anxious"}
    assert labels["anxious"].voice_id == "v-anx" and labels["anxious"].tags == "[nervous]"
    assert labels["anxious"].voice_settings == {"speed": 1.1}


async def test_live_voices_are_the_production_adapter_and_need_both_keys() -> None:
    with pytest.raises(ConfigError, match="ARI_ELEVENLABS_API_KEY et ARI_STT_API_KEY"):
        build_voices(settings(), [], personas=False, live=True)
    setup = build_voices(
        settings(elevenlabs_api_key="el-key", stt_api_key="stt-key"),
        ["paul=v-paul"],
        personas=False,
        live=True,
    )
    assert [v.label for v in setup.voices] == ["paul"]
    assert isinstance(setup.voices[0].tts, ElevenLabsStreamingTTSProvider)
    assert setup.stt_provider == "api.groq.com" and setup.stt_model == "whisper-large-v3"
    await setup.close()


@pytest.mark.parametrize("value", ["paul", "=v1", "../x=v1", "paul="])
def test_candidate_voices_need_a_safe_name_and_an_id(value: str) -> None:
    with pytest.raises(ConfigError):
        parse_candidate(value)


def test_duplicate_voice_names_are_refused() -> None:
    with pytest.raises(ConfigError, match="double"):
        build_voices(settings(), ["a=v1", "a=v2"], personas=False, live=False)
