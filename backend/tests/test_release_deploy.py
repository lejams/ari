"""Regression checks for the immutable release shell entrypoint.

The tests use a fake Docker executable: they prove refusal paths do not mutate Docker and that the
successful production ordering keeps pull and backup ahead of the one-shot migration.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "ghcr.io/lejams/ari@sha256:" + "a" * 64
SHA = "b" * 40


@pytest.fixture
def release_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    (bundle / "deploy").mkdir(parents=True)
    shutil.copy(ROOT / "docker-compose.yml", bundle / "docker-compose.yml")
    for name in (
        "release.sh", "staging.compose.yml", "backup.sh", "lock.sh", "provider_secrets.py"
    ):
        target = bundle / "deploy" / name
        shutil.copy(ROOT / "deploy" / name, target)
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
    return bundle


@pytest.fixture
def environment_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "environment"
    directory.mkdir()
    (directory / ".env").write_text(
        "COMPOSE_PROJECT_NAME=ari\nPOSTGRES_PASSWORD=test\n"
        "ARI_PLATFORM_DB_PASSWORD=test\nARI_CONTENT_DB_PASSWORD=test\n"
    )
    return directory


def run_release(
    bundle: Path, environment: Path, bin_dir: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(
        [str(bundle / "deploy" / "release.sh"), *extra],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def set_staging_project(environment: Path) -> None:
    env_file = environment / ".env"
    env_file.write_text(
        env_file.read_text().replace(
            "COMPOSE_PROJECT_NAME=ari\n", "COMPOSE_PROJECT_NAME=ari-staging\n"
        )
    )


def fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "docker.log"
    executable = tmp_path / "docker"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'if [[ "${FAIL_SOURCE_FILES:-}" = 1 && "$*" == *\'run --rm -i\'* ]]; then exit 1; fi\n'
        'if [[ "${FAIL_ACTIVATION:-}" = 1 && "$*" == *\'--no-deps\'* ]]; then exit 1; fi\n'
        'if [ "${FAIL_POSTGRES_VOLUME:-}" = 1 ] && '
        "[ \"$*\" = 'volume inspect ari_postgres_data' ]; then exit 1; fi\n"
        'case "$*" in\n'
        "  'compose version --short') echo \"${FAKE_COMPOSE_VERSION:-2.24.4}\" ;;\n"
        "  *' ps --status running --services'*) echo postgres ;;\n"
        "  *' exec -T postgres psql '*) echo inventory ;;\n"
        "  *'docker run'*|run\\ --rm*) echo 3 ;;\n"
        "esac\n"
    )
    executable.chmod(0o755)
    flock = tmp_path / "flock"
    flock.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'flock %s\\n\' "$*" >> "$DOCKER_LOG"\n'
        '[ "${FAIL_FLOCK:-}" != 1 ]\n'
    )
    flock.chmod(0o755)
    return tmp_path, log


def test_production_missing_or_stale_proof_rejects_before_docker_mutation(
    release_bundle: Path, environment_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, log = fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_LOG", str(log))
    missing = run_release(
        release_bundle, environment_dir, bin_dir, "production", str(environment_dir), IMAGE, SHA
    )
    assert missing.returncode != 0
    assert "staging-success.json" in missing.stderr
    assert log.read_text().splitlines() == ["flock -n 9"]

    (release_bundle / "staging-success.json").write_text(
        json.dumps({"sha": "c" * 40, "image": IMAGE})
    )
    stale = run_release(
        release_bundle, environment_dir, bin_dir, "production", str(environment_dir), IMAGE, SHA
    )
    assert stale.returncode != 0
    assert "does not match" in stale.stderr
    assert log.read_text().splitlines() == ["flock -n 9", "flock -n 9"]


def test_production_missing_live_data_rejects_before_pull_or_backup(
    release_bundle: Path, environment_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, log = fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_LOG", str(log))
    monkeypatch.setenv("FAIL_POSTGRES_VOLUME", "1")
    (release_bundle / "staging-success.json").write_text(json.dumps({"sha": SHA, "image": IMAGE}))
    result = run_release(
        release_bundle, environment_dir, bin_dir, "production", str(environment_dir), IMAGE, SHA
    )
    assert result.returncode != 0
    assert "missing ari_postgres_data" in result.stderr
    assert log.read_text().splitlines() == [
        "flock -n 9",
        "compose version --short",
        "volume inspect ari_postgres_data",
    ]
    assert not (environment_dir / ".release.json").exists()


def test_production_backs_up_before_migration_and_marks_only_success(
    release_bundle: Path, environment_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, log = fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_LOG", str(log))
    (release_bundle / "staging-success.json").write_text(json.dumps({"sha": SHA, "image": IMAGE}))
    backup = release_bundle / "deploy" / "backup.sh"
    backup.write_text('#!/usr/bin/env bash\nprintf \'backup %s\\n\' "$*" >> "$DOCKER_LOG"\n')
    backup.chmod(0o755)

    result = run_release(
        release_bundle, environment_dir, bin_dir, "production", str(environment_dir), IMAGE, SHA
    )
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    migration = next(
        i
        for i, line in enumerate(lines)
        if " up -d --no-build --wait" in line and line.endswith(" migrate")
    )
    assert lines.index("backup predeploy-" + SHA) < migration
    assert not any(" build" in line or " down" in line or " reset" in line for line in lines)
    assert json.loads((environment_dir / ".release.json").read_text()) == {
        "sha": SHA,
        "image": IMAGE,
    }


def test_release_runtime_declares_distinct_persistent_staging_shape() -> None:
    release = (ROOT / "deploy" / "release.sh").read_text()
    staging = (ROOT / "deploy" / "staging.compose.yml").read_text()
    assert 'project="ari-staging"' in release
    assert "127.0.0.1:18000:8000" in staging
    assert "127.0.0.1:18100:8100" in staging
    assert "127.0.0.1:15432:5432" in staging
    assert "ARI_PROVIDER_MODE: fake" in staging


def test_missing_referenced_pdf_blocks_production_before_pull(
    release_bundle: Path, environment_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, log = fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_LOG", str(log))
    monkeypatch.setenv("FAIL_SOURCE_FILES", "1")
    (release_bundle / "staging-success.json").write_text(json.dumps({"sha": SHA, "image": IMAGE}))
    result = run_release(
        release_bundle, environment_dir, bin_dir, "production", str(environment_dir), IMAGE, SHA
    )
    assert result.returncode != 0
    assert not any(" pull " in line or " up " in line for line in log.read_text().splitlines())
    assert not (environment_dir / ".release.json").exists()


def test_failed_activation_quiesces_all_writers_and_records_recovery_state(
    release_bundle: Path, environment_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, log = fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_LOG", str(log))
    monkeypatch.setenv("FAIL_ACTIVATION", "1")
    set_staging_project(environment_dir)
    previous = {"sha": "c" * 40, "image": IMAGE}
    for name in (".release.json", ".staging-success.json"):
        (environment_dir / name).write_text(json.dumps(previous))
    result = run_release(
        release_bundle, environment_dir, bin_dir, "staging", str(environment_dir), IMAGE, SHA
    )
    assert result.returncode != 0
    for name in (".release.json", ".staging-success.json"):
        assert json.loads((environment_dir / name).read_text()) == previous
    assert (environment_dir / ".release-failure.json").exists(), result.stderr + log.read_text()
    failure = json.loads((environment_dir / ".release-failure.json").read_text())
    assert failure == {"sha": SHA, "image": IMAGE, "phase": "activate", "previous": previous}
    lines = log.read_text().splitlines()
    stopped = next(
        i for i, line in enumerate(lines) if line.endswith(" stop app backoffice worker")
    )
    migration = next(i for i, line in enumerate(lines) if line.endswith(" migrate"))
    assert stopped < migration


def test_production_activation_failure_preserves_success_and_reports_phase(
    release_bundle: Path, environment_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, log = fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_LOG", str(log))
    monkeypatch.setenv("FAIL_ACTIVATION", "1")
    previous = {"sha": "c" * 40, "image": IMAGE}
    (environment_dir / ".release.json").write_text(json.dumps(previous))
    (release_bundle / "staging-success.json").write_text(json.dumps({"sha": SHA, "image": IMAGE}))
    backup = release_bundle / "deploy" / "backup.sh"
    backup.write_text('#!/usr/bin/env bash\nprintf \'backup %s\\n\' "$*" >> "$DOCKER_LOG"\n')
    backup.chmod(0o755)

    result = run_release(
        release_bundle, environment_dir, bin_dir, "production", str(environment_dir), IMAGE, SHA
    )
    assert result.returncode != 0
    assert json.loads((environment_dir / ".release.json").read_text()) == previous
    assert json.loads((environment_dir / ".release-failure.json").read_text()) == {
        "sha": SHA,
        "image": IMAGE,
        "phase": "activate",
        "previous": previous,
    }
    lines = log.read_text().splitlines()
    assert next(
        i for i, line in enumerate(lines) if line.endswith(" stop app backoffice worker")
    ) < next(i for i, line in enumerate(lines) if line.endswith(" migrate"))


def test_lock_failure_stops_before_docker_compose(
    release_bundle: Path, environment_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, log = fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_LOG", str(log))
    monkeypatch.setenv("FAIL_FLOCK", "1")
    set_staging_project(environment_dir)
    result = run_release(
        release_bundle, environment_dir, bin_dir, "staging", str(environment_dir), IMAGE, SHA
    )
    assert result.returncode != 0
    assert "another deployment" in result.stderr
    assert log.read_text().splitlines() == ["flock -n 9"]


def test_provider_secrets_are_installed_under_the_deploy_lock_before_activation(
    release_bundle: Path, environment_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, log = fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_LOG", str(log))
    activation_env = tmp_path / "activation.env"
    monkeypatch.setenv("PROVIDER_ENV", str(environment_dir / ".env"))
    monkeypatch.setenv("ACTIVATION_ENV", str(activation_env))
    docker = bin_dir / "docker"
    docker.write_text(docker.read_text().replace(
        "set -eu\n",
        'set -eu\nif [[ "$*" == *--no-deps* ]]; then '
        'cp "$PROVIDER_ENV" "$ACTIVATION_ENV"; fi\n',
        1,
    ))
    (release_bundle / "staging-success.json").write_text(json.dumps({"sha": SHA, "image": IMAGE}))
    backup = release_bundle / "deploy" / "backup.sh"
    backup.write_text("#!/usr/bin/env bash\ncat >/dev/null\n")
    backup.chmod(0o755)
    payload = json.dumps({"ARI_OPENAI_API_KEY": "openai-key", "ARI_STT_API_KEY": "groq-key"})
    result = subprocess.run(
        [
            str(release_bundle / "deploy" / "release.sh"),
            "production", str(environment_dir), IMAGE, SHA, "--provider-secrets-stdin",
        ],
        input=payload,
        text=True,
        capture_output=True,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    contents = (environment_dir / ".env").read_text()
    assert "ARI_OPENAI_API_KEY=openai-key" in contents
    assert "ARI_STT_API_KEY=groq-key" in contents
    assert activation_env.read_text() == contents
    assert " up -d --no-build --no-deps" in log.read_text()


def test_provider_secret_payload_is_not_applied_when_lock_is_busy(
    release_bundle: Path, environment_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, _ = fake_docker(tmp_path)
    monkeypatch.setenv("FAIL_FLOCK", "1")
    before = (environment_dir / ".env").read_text()
    result = subprocess.run(
        [
            str(release_bundle / "deploy" / "release.sh"),
            "production", str(environment_dir), IMAGE, SHA, "--provider-secrets-stdin",
        ],
        input=json.dumps({"ARI_OPENAI_API_KEY": "one", "ARI_STT_API_KEY": "two"}),
        text=True,
        capture_output=True,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        check=False,
    )
    assert result.returncode != 0
    assert (environment_dir / ".env").read_text() == before


def test_provider_secret_helper_replaces_only_secret_lines_atomically(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OTHER=value\nARI_OPENAI_API_KEY=old\nARI_STT_API_KEY=old\n"
        " export ARI_STT_API_KEY = duplicate\n  ARI_OPENAI_API_KEY = duplicate\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "deploy" / "provider_secrets.py"),
            "--install-stdin",
            str(env_file),
        ],
        input=json.dumps({"ARI_OPENAI_API_KEY": "new-openai", "ARI_STT_API_KEY": "new+/stt=="}),
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert env_file.read_text() == (
        "OTHER=value\nARI_OPENAI_API_KEY=new-openai\nARI_STT_API_KEY=new+/stt==\n"
    )
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


@pytest.mark.parametrize("stale_proof", [False, True])
def test_provider_sync_invalid_proof_preserves_environment(
    release_bundle: Path, environment_dir: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, stale_proof: bool,
) -> None:
    bin_dir, log = fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_LOG", str(log))
    if stale_proof:
        (release_bundle / "staging-success.json").write_text(
            json.dumps({"sha": "c" * 40, "image": IMAGE})
        )
    before = (environment_dir / ".env").read_bytes()
    result = subprocess.run(
        [str(release_bundle / "deploy/release.sh"), "production", str(environment_dir),
         IMAGE, SHA, "--provider-secrets-stdin"],
        input=json.dumps({"ARI_OPENAI_API_KEY": "new-openai", "ARI_STT_API_KEY": "new-stt"}),
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert (environment_dir / ".env").read_bytes() == before
    assert log.read_text().splitlines() == ["flock -n 9"]


def test_provider_secret_helper_rejects_malicious_newlines_without_mutating(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=value\n")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "deploy" / "provider_secrets.py"),
            "--install-stdin",
            str(env_file),
        ],
        input=json.dumps({"ARI_OPENAI_API_KEY": "safe\nINJECTED=yes", "ARI_STT_API_KEY": "ok"}),
        text=True, capture_output=True, check=False,
    )
    assert result.returncode != 0
    assert "INJECTED=yes" not in result.stderr
    assert env_file.read_text() == "OTHER=value\n"


def test_legacy_deploy_and_live_restore_share_the_root_lock() -> None:
    deploy = (ROOT / "deploy" / "deploy.sh").read_text()
    restore = (ROOT / "deploy" / "restore.sh").read_text()
    assert 'acquire_deploy_lock "$PWD/.deploy.lock"' in deploy
    assert 'acquire_deploy_lock "$PWD/.deploy.lock"' in restore


def test_old_compose_refuses_staging_before_container_changes(
    release_bundle: Path, environment_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir, log = fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_COMPOSE_VERSION", "2.20.0")
    set_staging_project(environment_dir)
    result = run_release(
        release_bundle, environment_dir, bin_dir, "staging", str(environment_dir), IMAGE, SHA
    )
    assert result.returncode != 0
    assert ">= 2.24.4" in result.stderr
    assert log.read_text().splitlines() == ["flock -n 9", "compose version --short"]

