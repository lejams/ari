"""The way into the bank: a batch is imported only after every check is run again."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import build_test_container
from exercise_scripts import (
    AUTHOR,
    CHECKER,
    ScriptedModels,
    judge_passes,
    live_config,
    passing_gate_report,
    solve_as,
)
from test_exercise_pipeline import DRAFT, OTHER, author_script, checker_over
from test_language_exercises_domain import CLOZE, candidate

from ari.application.exercise_batch import ExerciseBatch
from ari.application.services.exercise_bank import ExerciseAuthor, ExerciseFactory, ExerciseSpec
from ari.domain.errors import InvalidStateError
from ari.domain.language_exercises import LanguageExercise
from ari.evals.exercises.gate import GateRefusedError
from ari.evals.report import Report
from ari.exercises_cli import import_batch, parse_spec, run
from ari.infrastructure.exercises.models import load_prompts
from ari.infrastructure.persistence.platform.exercises import SqlExerciseBank

PROMPTS = load_prompts(live_config())


@pytest.fixture(scope="module")
def gate_report() -> Report:
    return passing_gate_report()


def solver_by_text(request: Any, payload: dict[str, Any]) -> dict[str, Any]:
    texts = [o["text"] for o in payload["options"]]
    return solve_as(texts.index("die" if "Haben Sie" in payload["stem"] else "eine"))(
        request, payload
    )


async def make_batch() -> ExerciseBatch:
    provider = ScriptedModels(
        author=author_script([DRAFT, OTHER]), solver=solver_by_text, judge=judge_passes()
    )
    factory = ExerciseFactory(ExerciseAuthor(provider, PROMPTS["author"]), checker_over(provider))
    batch = await factory.run("batch-1", [(ExerciseSpec("B1", "gender", "cloze"), 2)])
    assert len(batch.accepted) == 2
    return batch.model_copy(update={"config_sha256": "0" * 64})  # The scripted gate report's.


@pytest.fixture
def report_file(tmp_path: Path, gate_report: Report) -> Path:
    path = tmp_path / "report.json"
    path.write_text(gate_report.model_dump_json(), encoding="utf-8")
    return path


@pytest.fixture
def bank(database_url: str) -> Any:
    container = build_test_container(database_url)
    yield SqlExerciseBank(container.repository.engine)
    container.repository.engine.dispose()


async def test_a_batch_backed_by_a_passing_gate_enters_the_bank_with_its_checks(
    bank: SqlExerciseBank, report_file: Path
) -> None:
    batch = await make_batch()
    result = import_batch(batch, bank, gate_report_path=report_file)
    assert (result["nouveaux"], result["deja_presents"]) == (2, 0)
    stored = bank.list_active()
    assert len(stored) == 2
    first = stored[0]
    assert first.batch_id == "batch-1"
    assert first.provenance["author"]["model"] == AUTHOR[1]
    assert first.provenance["solver"]["model"] == CHECKER[1]
    assert first.provenance["gate_report"]["suite"] == "language-exercises@1"
    reviews = bank.reviews(first.exercise.exercise_id)
    assert [r.reviewer_kind for r in reviews] == ["blind_solver", "rubric_judge"]
    assert reviews[1].reviewer_name == "anthropic:claude-sonnet-5-5@exercise-rubric-v3"
    again = import_batch(batch, bank, gate_report_path=report_file)
    assert (again["nouveaux"], again["deja_presents"]) == (0, 2)


async def test_a_batch_judged_with_another_rubric_or_checks_than_the_code_applies(
    bank: SqlExerciseBank, report_file: Path
) -> None:
    batch = await make_batch()
    for change in ({"rubric_version": "exercise-rubric-v1"}, {"mechanical_checks_version": "x"}):
        with pytest.raises(InvalidStateError, match="ce code applique"):
            import_batch(batch.model_copy(update=change), bank, gate_report_path=report_file)
    assert bank.list_active() == ()


async def test_a_dry_run_writes_nothing(bank: SqlExerciseBank, report_file: Path) -> None:
    result = import_batch(await make_batch(), bank, gate_report_path=report_file, dry_run=True)
    assert result["a_importer"] == 2 and bank.list_active() == ()


async def test_a_sentence_already_taken_refuses_the_whole_batch_before_any_write(
    bank: SqlExerciseBank, report_file: Path
) -> None:
    batch = await make_batch()
    # Another exercise, with the same correct sentence as the batch's second one, is in the bank.
    twin = LanguageExercise.model_validate_json(
        json.dumps({**batch.accepted[1].exercise, "explanation_fr": "Une autre explication, ici."})
    )
    bank.add(twin, batch_id="older", provenance={}, reviews=[])
    for dry_run in (True, False):
        with pytest.raises(InvalidStateError, match="sous un autre exercice"):
            import_batch(batch, bank, gate_report_path=report_file, dry_run=dry_run)
    # Nothing of the batch was written: not even the exercise that came first.
    assert [s.exercise.exercise_id for s in bank.list_active()] == [twin.exercise_id]


async def test_a_dry_run_counts_what_is_already_in_the_bank(
    bank: SqlExerciseBank, report_file: Path
) -> None:
    batch = await make_batch()
    import_batch(batch, bank, gate_report_path=report_file)
    result = import_batch(batch, bank, gate_report_path=report_file, dry_run=True)
    assert (result["a_importer"], result["deja_presents"]) == (0, 2)


async def test_the_same_sentence_twice_in_a_batch_is_refused(
    bank: SqlExerciseBank, report_file: Path
) -> None:
    batch = await make_batch()
    twice = batch.model_copy(update={"accepted": [batch.accepted[0], batch.accepted[0]]})
    with pytest.raises(InvalidStateError, match="deux fois dans le lot"):
        import_batch(twice, bank, gate_report_path=report_file)
    assert bank.list_active() == ()


async def test_without_a_passing_gate_nothing_is_imported(
    bank: SqlExerciseBank, tmp_path: Path, gate_report: Report
) -> None:
    failed = gate_report.model_copy(
        update={"gate": gate_report.gate.model_copy(update={"status": "failed"})}  # type: ignore[union-attr]
    )
    path = tmp_path / "failed.json"
    path.write_text(failed.model_dump_json(), encoding="utf-8")
    with pytest.raises(GateRefusedError, match="pas passé"):
        import_batch(await make_batch(), bank, gate_report_path=path)
    assert bank.list_active() == ()


async def test_a_batch_that_was_not_made_by_real_models_is_refused(
    bank: SqlExerciseBank, report_file: Path
) -> None:
    batch = await make_batch()
    fake = batch.model_copy(update={"author": batch.author.model_copy(update={"provider": "fake"})})
    with pytest.raises(InvalidStateError, match="vrai modèle"):
        import_batch(fake, bank, gate_report_path=report_file)
    same = batch.model_copy(
        update={
            "solver": batch.solver.model_copy(
                update=batch.author.model_dump(include={"provider", "model"})
            )
        }
    )
    with pytest.raises(InvalidStateError, match="même modèle"):
        import_batch(same, bank, gate_report_path=report_file)
    assert bank.list_active() == ()


async def test_an_edited_batch_is_refused_whole(bank: SqlExerciseBank, report_file: Path) -> None:
    batch = await make_batch()
    edited = batch.accepted[1].model_copy(
        update={
            "exercise": {
                **batch.accepted[1].exercise,
                "explanation_fr": "Une autre explication, plus longue.",
            }
        }
    )
    with pytest.raises(InvalidStateError, match="ne correspond pas au contenu"):
        import_batch(
            batch.model_copy(update={"accepted": [batch.accepted[0], edited]}),
            bank,
            gate_report_path=report_file,
        )
    unapproved = batch.accepted[1].model_copy(
        update={
            "solver": {
                **batch.accepted[1].solver,
                "acceptable_indices": [
                    batch.accepted[1].solver["chosen_index"],
                    3 if batch.accepted[1].solver["chosen_index"] != 3 else 0,
                ],
            }
        }
    )
    with pytest.raises(InvalidStateError, match="ne l'approuvent pas"):
        import_batch(
            batch.model_copy(update={"accepted": [batch.accepted[0], unapproved]}),
            bank,
            gate_report_path=report_file,
        )
    assert bank.list_active() == ()  # The first, good, exercise was not written either.


def test_a_spec_is_checked_before_any_call() -> None:
    spec, count = parse_spec("B1:case:cloze:10")
    assert (spec.level, spec.category, spec.format, count) == ("B1", "case", "cloze", 10)
    for bad in (
        "A2:case:cloze:5",
        "B1:other:cloze:5",
        "B1:case:essay:5",
        "B1:case:cloze:0",
        "B1:case",
    ):
        with pytest.raises(ValueError):
            parse_spec(bad)


def test_the_command_line_lists_and_withdraws(
    bank: SqlExerciseBank, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    item = candidate(CLOZE)
    from ari.domain.language_exercises import LanguageExercise

    exercise = LanguageExercise.model_validate_json(item.model_dump_json())
    bank.add(exercise, batch_id="b", provenance={}, reviews=[])
    assert run(["--database-url", database_url, "list", "--level", "B1"]) == 0
    assert exercise.exercise_id in capsys.readouterr().out
    argv = ["--database-url", database_url, "withdraw", exercise.exercise_id]
    assert run([*argv, "--reason", "Deux réponses possibles", "--actor", "Ismail"]) == 0
    assert run([*argv, "--reason", "encore", "--actor", "Ismail"]) == 2  # Final.
    assert bank.list_active() == ()


def test_the_command_line_generates_a_batch_file_without_touching_the_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "batch.json"
    argv = ["generate", "--config", "evals/configs/exercises/fake.yaml", "--no-bank"]
    code = run([*argv, "--spec", "B1:case:cloze:3", "--out", str(out)])
    assert code == 0
    batch = ExerciseBatch.model_validate_json(out.read_text(encoding="utf-8"))
    assert batch.author.provider == "fake" and batch.accepted
    assert json.loads(capsys.readouterr().out)["lot"] == batch.batch_id
    # A batch of the simulated chain can never enter the bank.
    assert run(["import", str(out), "--gate-report", str(out), "--dry-run"]) == 2


async def test_the_import_follows_the_judges_level_and_refuses_what_does_not(
    bank: SqlExerciseBank, report_file: Path
) -> None:
    batch = await make_batch()
    entry = batch.accepted[0]
    # The scripted judge reads B2 and the spec was B1: stored as B2, relabelled from B1.
    assert (entry.exercise["level"], entry.level_relabeled_from) == ("B2", "B1")
    assert entry.judge["estimated_level"] == "B2"
    # Stored under a level the judge did not read: refused, whatever the identifier says.
    other = {**entry.exercise, "level": "C1"}
    other_id = LanguageExercise.model_validate_json(json.dumps(other)).exercise_id
    wrong = entry.model_copy(update={"exercise": other, "exercise_id": other_id})
    # The judge reads C1 and the exercise is stored as C1, but the author declared B1: two steps.
    far = entry.model_copy(
        update={
            "exercise": other,
            "exercise_id": other_id,
            "judge": {**entry.judge, "estimated_level": "C1"},
            "level_relabeled_from": "B1",
        }
    )
    for tampered in (wrong, far):
        bad = batch.model_copy(update={"accepted": [tampered, *batch.accepted[1:]]})
        with pytest.raises(InvalidStateError, match="estimation du juge"):
            import_batch(bad, bank, gate_report_path=report_file)
    assert bank.list_active() == ()
    import_batch(batch, bank, gate_report_path=report_file)
    assert {s.exercise.level for s in bank.list_active()} == {"B2"}


def test_the_generation_default_is_the_configuration_the_gate_workflow_measures() -> None:
    from ari.exercises_cli import DEFAULT_CONFIG

    # Haiku only (owner decision of 5 November 2026): a batch made without `--config` must be
    # importable with the gate report of the only configuration the workflow offers.
    assert DEFAULT_CONFIG.name == "luna-haiku.yaml" and DEFAULT_CONFIG.is_file()
