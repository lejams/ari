"""Committed datasets: integrity, synthetic only, family splits, sealed holdout."""

from __future__ import annotations

import hashlib
import json
import re
import runpy
import shutil
from pathlib import Path

import pytest
from scripted import DATASETS, PROMPTS

from ari.evals.manifest import DatasetError, HoldoutAccessError, load_dataset
from ari.evals.scorers.evaluation_language import LanguageItem
from ari.evals.scorers.patient_leakage import LeakageItem, PatientLeakageSuite
from ari.evals.suites import SUITES

COMMITTED = [("patient-leakage", "1"), ("evaluation-language", "1")]


def _normalize(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def _utterances(row: dict[str, object]) -> list[str]:
    texts: list[str] = []
    if "utterance" in row:
        texts.append(str(row["utterance"]))
    for key in ("history", "turns"):
        for turn in row.get(key, []):  # type: ignore[attr-defined]
            texts.append(str(turn["doctor"]))
    return texts


@pytest.mark.parametrize(("suite", "version"), COMMITTED)
def test_committed_datasets_match_their_manifest_and_are_synthetic(
    suite: str, version: str
) -> None:
    dataset = load_dataset(DATASETS, suite, version)
    assert dataset.manifest.synthetic is True
    assert dataset.manifest.consent == "not_applicable:synthetic"
    assert set(dataset.manifest.splits["dev"]).isdisjoint(dataset.manifest.splits["holdout"])
    items = [
        SUITES[suite].parse_item(row)
        for split in ("dev", "holdout")
        for row in dataset.read_split(split, gate=True)  # type: ignore[arg-type]
    ]
    assert {item.family for item in items} == {
        *dataset.manifest.splits["dev"],
        *dataset.manifest.splits["holdout"],
    }
    for item in items:
        assert item.case_id in dataset.cases


def test_every_repository_dataset_is_synthetic() -> None:
    for manifest in DATASETS.glob("*/*/manifest.json"):
        assert json.loads(manifest.read_text("utf-8"))["synthetic"] is True, manifest


def test_the_build_script_reproduces_the_committed_files(tmp_path: Path) -> None:
    script = DATASETS / "build_synthetic_v1.py"
    module = runpy.run_path(str(script))
    module["main"](tmp_path)
    for suite, version in COMMITTED:
        for name in ("manifest.json", "cases.json", "dev.jsonl", "holdout.jsonl"):
            built = (tmp_path / suite / version / name).read_bytes()
            committed = (DATASETS / suite / version / name).read_bytes()
            assert built == committed, f"{suite}/{version}/{name}: rebuild differs"


def test_language_dataset_size_and_truth_by_construction() -> None:
    dataset = load_dataset(DATASETS, "evaluation-language", "1")
    rows = [*dataset.read_split("dev", gate=True), *dataset.read_split("holdout", gate=True)]
    assert 100 <= len(rows) <= 150  # Plan: 100 to 150 transcripts.
    categories = set()
    for row in rows:
        item = LanguageItem.model_validate_json(json.dumps(row))
        turns = {turn.sequence: turn for turn in item.turns}
        for error in item.truth.language_errors:
            categories.add(error.category)
            assert turns[error.turn].doctor != error.correct
        for switch in item.truth.code_switches:
            assert turns[switch.turn].patient_response_kind.value == "wrong_language"
            assert not turns[switch.turn].revealed_fact_ids
        case = dataset.cases[item.case_id]
        heard = {f for t in item.turns for f in t.revealed_fact_ids}
        expected_missing = [s.id for s in case.anamnesis_sections if not heard & set(s.fact_ids)]
        assert item.truth.missing_sections == expected_missing
    assert categories == {"gender", "case", "verb_form", "word_order"}


def test_leakage_items_reference_known_facts() -> None:
    dataset = load_dataset(DATASETS, "patient-leakage", "1")
    suite = PatientLeakageSuite()
    for split in ("dev", "holdout"):
        for row in dataset.read_split(split, gate=True):  # type: ignore[arg-type]
            item = LeakageItem.model_validate_json(json.dumps(row))
            suite.validate_against(item, dataset.cases[item.case_id].to_medical_case())


def test_holdout_is_refused_outside_gate_mode() -> None:
    dataset = load_dataset(DATASETS, "patient-leakage", "1")
    with pytest.raises(HoldoutAccessError):
        dataset.read_split("holdout", gate=False)
    assert dataset.read_split("dev", gate=False)


@pytest.mark.parametrize(("suite", "version"), COMMITTED)
def test_holdout_never_appears_in_a_prompt_nor_in_the_dev_split(suite: str, version: str) -> None:
    """Plan: the holdout is never used in few-shot. Checked by hash of normalized sentences."""
    dataset = load_dataset(DATASETS, suite, version)
    holdout = {
        hashlib.sha256(_normalize(text).encode()).hexdigest(): text
        for row in dataset.read_split("holdout", gate=True)
        for text in _utterances(row)
    }
    dev = {
        hashlib.sha256(_normalize(text).encode()).hexdigest()
        for row in dataset.read_split("dev", gate=True)
        for text in _utterances(row)
    }
    assert not set(holdout) & dev
    for prompt in PROMPTS.rglob("*.txt"):
        content = prompt.read_text("utf-8")
        sentences = {
            hashlib.sha256(_normalize(s).encode()).hexdigest()
            for s in re.split(r"[\n.?!]+", content)
            if s.strip()
        }
        normalized = _normalize(content)
        for digest, text in holdout.items():
            assert digest not in sentences, f"{prompt.name} contains a holdout sentence"
            assert _normalize(text) not in normalized, f"{prompt.name} quotes {text!r}"


def test_a_modified_data_file_stops_the_run(tmp_path: Path) -> None:
    copy = tmp_path / "patient-leakage" / "1"
    shutil.copytree(DATASETS / "patient-leakage" / "1", copy)
    with (copy / "dev.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(DatasetError, match="empreinte"):
        load_dataset(tmp_path, "patient-leakage", "1")


def test_a_row_outside_its_split_families_is_rejected(tmp_path: Path) -> None:
    copy = tmp_path / "patient-leakage" / "1"
    shutil.copytree(DATASETS / "patient-leakage" / "1", copy)
    holdout_row = (copy / "holdout.jsonl").read_text("utf-8").splitlines()[0]
    (copy / "dev.jsonl").write_text(holdout_row + "\n", encoding="utf-8")
    manifest = json.loads((copy / "manifest.json").read_text("utf-8"))
    manifest["files"]["dev.jsonl"] = hashlib.sha256((copy / "dev.jsonl").read_bytes()).hexdigest()
    (copy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    dataset = load_dataset(tmp_path, "patient-leakage", "1")
    with pytest.raises(DatasetError, match="split"):
        dataset.read_split("dev", gate=False)


def test_a_real_dataset_needs_a_recorded_consent(tmp_path: Path) -> None:
    copy = tmp_path / "patient-leakage" / "1"
    shutil.copytree(DATASETS / "patient-leakage" / "1", copy)
    manifest = json.loads((copy / "manifest.json").read_text("utf-8"))
    manifest["synthetic"] = False
    (copy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="consent"):
        load_dataset(tmp_path, "patient-leakage", "1")
