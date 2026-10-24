from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[2]

def load_workflow(path: str) -> dict[str, Any]:
    # BaseLoader preserves GitHub's YAML key "on" as a string.
    with (ROOT / path).open(encoding="utf-8") as file:
        return cast(dict[str, Any], yaml.load(file, Loader=yaml.BaseLoader))

def test_ci_keeps_quality_and_uses_immutable_staging_flow() -> None:
    workflow = load_workflow(".github/workflows/ci.yml")
    assert "workflow_dispatch" in workflow["on"]
    jobs = workflow["jobs"]
    assert "postgres" in jobs["quality"]["services"]
    assert jobs["image"]["permissions"]["packages"] == "write"
    image_steps = str(jobs["image"]["steps"])
    assert "docker/build-push-action@v6" in image_steps
    assert "ghcr.io/lejams/ari:${{ github.sha }}" in image_steps
    bundle_steps = str(jobs["bundle"]["steps"])
    assert "deploy/backup.sh" in bundle_steps
    assert "deploy/release.sh" in bundle_steps
    assert "deploy/postgres/init" in bundle_steps
    assert jobs["staging"]["environment"] == "staging"
    assert jobs["staging"]["permissions"]["packages"] == "read"
    transfer = next(
        step for step in jobs["staging"]["steps"]
        if "Transfer and activate" in step.get("name", "")
    )
    assert transfer["env"]["GHCR_USERNAME"] == "${{ github.actor }}"
    assert transfer["env"]["GHCR_TOKEN"] == "${{ github.token }}"
    assert jobs["staging"]["concurrency"]["cancel-in-progress"] == "false"
    assert "staged-release" in str(jobs["staging"])
    assert "include-hidden-files" in str(jobs["staging"])

def test_promotion_is_manual_main_only_and_validates_staging_proof() -> None:
    workflow = load_workflow(".github/workflows/promote.yml")
    assert set(workflow["on"]) == {"workflow_dispatch"}
    production = workflow["jobs"]["production"]
    assert production["environment"] == "production"
    assert production["permissions"]["packages"] == "read"
    transfer = next(
        step for step in production["steps"]
        if step.get("name") == "Transfer the staged artifact to production"
    )
    assert transfer["env"]["GHCR_USERNAME"] == "${{ github.actor }}"
    assert transfer["env"]["GHCR_TOKEN"] == "${{ github.token }}"
    assert "refs/heads/main" in production["if"]
    steps = str(production["steps"])
    assert "actions/runs/$RUN_ID" in steps
    assert "workflow_id" in steps
    assert "merge-base --is-ancestor" in steps
    assert "staging-success.json" in steps
    assert "workflow_run" not in steps

def test_promotion_requires_the_evals_gate_before_deploying_a_changed_ai() -> None:
    production = load_workflow(".github/workflows/promote.yml")["jobs"]["production"]
    names = [step.get("name", "") for step in production["steps"]]
    gate = names.index("Require the ari.evals gate when the AI changed")
    assert gate < names.index("Transfer the staged artifact to production")
    script = production["steps"][gate]["run"]
    assert "ari.evals fingerprint --root candidate-tree" in script
    assert "evals-live.yml" in script and "merge-base --is-ancestor" in script
    assert '.status eval-gate/verdict.json)" = passed' in script
    assert '.fingerprint eval-gate/verdict.json)" = "$fingerprint"' in script
    assert "production-release" in str(production["steps"][-1])


def test_live_evals_are_manual_main_only_with_scoped_keys() -> None:
    workflow = load_workflow(".github/workflows/evals-live.yml")
    assert set(workflow["on"]) == {"workflow_dispatch"}
    job = workflow["jobs"]["evals"]
    assert "refs/heads/main" in job["if"] and job["environment"] == "evals"
    run_step = next(s for s in job["steps"] if s.get("name") == "Run the promotion suites")
    assert run_step["env"]["ARI_OPENAI_API_KEY"] == "${{ secrets.ARI_OPENAI_API_KEY }}"
    assert "promotion --split" in run_step["run"] and "--live" in run_step["run"]
    assert "eval-gate" in str(job["steps"][-1])


def test_transfer_helper_rejects_bad_metadata_before_ssh(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "release.json").write_text('{"sha":"bad","image":"bad"}', encoding="utf-8")
    result = subprocess.run(
        [str(ROOT / "deploy/transfer-release.sh"), "staging", str(bundle), "bad", "bad"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "commit SHA" in result.stderr


def test_transfer_helper_rejects_unsafe_host_before_ssh(tmp_path: Path) -> None:
    sha = "a" * 40
    image = "ghcr.io/lejams/ari@sha256:" + "b" * 64
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "release.json").write_text(
        f'{{"sha":"{sha}","image":"{image}"}}', encoding="utf-8"
    )
    result = subprocess.run(
        [str(ROOT / "deploy/transfer-release.sh"), "staging", str(bundle), image, sha],
        env=os.environ
        | {
            "DEPLOY_HOST": "host;touch-pwned",
            "DEPLOY_USER": "ari",
            "DEPLOY_PATH": "/opt/ari",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "invalid DEPLOY_HOST" in result.stderr
    assert not (tmp_path / "pwned").exists()


def test_transfer_helper_uses_pinned_image_and_scp_port(tmp_path: Path) -> None:
    sha = "a" * 40
    image = "ghcr.io/lejams/ari@sha256:" + "b" * 64
    bundle = tmp_path / "bundle"
    (bundle / "deploy").mkdir(parents=True)
    (bundle / "deploy" / "release.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (bundle / "release.json").write_text(
        f'{{"sha":"{sha}","image":"{image}"}}', encoding="utf-8"
    )
    marker = tmp_path / "marker.json"
    marker.write_text(f'{{"sha":"{sha}","image":"{image}"}}', encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "calls.log"
    (fake_bin / "ssh").write_text(
        "#!/usr/bin/env bash\nprintf 'ssh %s\\n' \"$*\" >> \"$CALL_LOG\"\n", encoding="utf-8"
    )
    (fake_bin / "scp").write_text(
        "#!/usr/bin/env bash\nprintf 'scp %s\\n' \"$*\" >> \"$CALL_LOG\"\n"
        "last=\"${!#}\"\n"
        "for arg in \"$@\"; do\n"
        "  [[ \"$arg\" == *.staging-success.json ]] && cp \"$MARKER\" \"$last\"\n"
        "done\nexit 0\n",
        encoding="utf-8",
    )
    for command in (fake_bin / "ssh", fake_bin / "scp"):
        command.chmod(0o755)
    result = subprocess.run(
        [str(ROOT / "deploy/transfer-release.sh"), "staging", str(bundle), image, sha],
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CALL_LOG": str(log),
            "MARKER": str(marker),
            "DEPLOY_HOST": "release.example",
            "DEPLOY_USER": "ari",
            "DEPLOY_PORT": "2222",
            "DEPLOY_PATH": "/opt/ari-staging",
            "SSH_KNOWN_HOSTS": "release.example ssh-ed25519 AAAA",
            "SSH_PRIVATE_KEY": "private-key",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "scp -i " in calls and " -P 2222 " in calls
    assert f"/releases/{sha}/deploy/release.sh staging /opt/ari-staging {image} {sha}" in calls
    staged_marker = (bundle / "staging-success.json").read_text(encoding="utf-8")
    assert staged_marker == marker.read_text(encoding="utf-8")


def test_transfer_helper_rejects_incomplete_or_invalid_registry_auth_before_ssh(
    tmp_path: Path,
) -> None:
    sha = "a" * 40
    image = "ghcr.io/lejams/ari@sha256:" + "b" * 64
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "release.json").write_text(
        f'{{"sha":"{sha}","image":"{image}"}}', encoding="utf-8"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    attempted_ssh = tmp_path / "attempted-ssh"
    (fake_bin / "ssh").write_text(
        "#!/usr/bin/env bash\ntouch \"$SSH_ATTEMPTED\"\n", encoding="utf-8"
    )
    (fake_bin / "ssh").chmod(0o755)
    base_env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SSH_ATTEMPTED": str(attempted_ssh),
        "DEPLOY_HOST": "release.example",
        "DEPLOY_USER": "ari",
        "DEPLOY_PATH": "/opt/ari",
        "SSH_KNOWN_HOSTS": "release.example ssh-ed25519 AAAA",
        "SSH_PRIVATE_KEY": "private-key",
    }
    for auth_env, message in (
        ({"GHCR_USERNAME": "ari"}, "provided together"),
        (
            {"GHCR_USERNAME": "invalid user", "GHCR_TOKEN": "fake-token"},
            "invalid GHCR_USERNAME",
        ),
    ):
        result = subprocess.run(
            [str(ROOT / "deploy/transfer-release.sh"), "production", str(bundle), image, sha],
            env=base_env | auth_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert message in result.stderr
        assert not attempted_ssh.exists()


def test_transfer_helper_uses_ephemeral_registry_auth_and_removes_it(tmp_path: Path) -> None:
    sha = "a" * 40
    image = "ghcr.io/lejams/ari@sha256:" + "b" * 64
    bundle = tmp_path / "bundle"
    release = bundle / "deploy" / "release.sh"
    release.parent.mkdir(parents=True)
    release.write_text(
        "#!/usr/bin/env bash\n"
        "test -d \"$DOCKER_CONFIG\"\n"
        "printf '%s' \"$DOCKER_CONFIG\" > \"$RELEASE_CONFIG\"\n"
        "exit \"${RELEASE_STATUS:-0}\"\n",
        encoding="utf-8",
    )
    (bundle / "release.json").write_text(
        f'{{"sha":"{sha}","image":"{image}"}}', encoding="utf-8"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh_log = tmp_path / "ssh.log"
    docker_log = tmp_path / "docker.log"
    token_capture = tmp_path / "token-capture"
    release_config = tmp_path / "release-config"
    (fake_bin / "ssh").write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$SSH_LOG\"\n"
        "command=\"${!#}\"\n"
        "bash -c \"$command\"\n",
        encoding="utf-8",
    )
    (fake_bin / "scp").write_text(
        "#!/usr/bin/env bash\n"
        "source=\"${@: -2:1}\"\n"
        "target=\"${!#}\"\n"
        "destination=\"${target#*:}\"\n"
        "mkdir -p \"$destination\"\n"
        "cp -R \"$source\"/. \"$destination\"/\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env bash\n"
        "IFS= read -r token || true\n"
        "printf '%s' \"$token\" > \"$TOKEN_CAPTURE\"\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n",
        encoding="utf-8",
    )
    for command in (fake_bin / "ssh", fake_bin / "scp", fake_bin / "docker"):
        command.chmod(0o755)
    deploy_path = tmp_path / "remote"
    fake_token = "fake-registry-token"
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SSH_LOG": str(ssh_log),
        "DOCKER_LOG": str(docker_log),
        "TOKEN_CAPTURE": str(token_capture),
        "RELEASE_CONFIG": str(release_config),
        "DEPLOY_HOST": "release.example",
        "DEPLOY_USER": "ari",
        "DEPLOY_PATH": str(deploy_path),
        "SSH_KNOWN_HOSTS": "release.example ssh-ed25519 AAAA",
        "SSH_PRIVATE_KEY": "private-key",
        "GHCR_USERNAME": "ari-actions",
        "GHCR_TOKEN": fake_token,
    }
    result = subprocess.run(
        [str(ROOT / "deploy/transfer-release.sh"), "production", str(bundle), image, sha],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert token_capture.read_text(encoding="utf-8") == fake_token
    assert docker_log.read_text(encoding="utf-8") == (
        "login ghcr.io --username ari-actions --password-stdin\n"
    )
    assert fake_token not in ssh_log.read_text(encoding="utf-8")
    assert not Path(release_config.read_text(encoding="utf-8")).exists()


def test_transfer_helper_cleans_ephemeral_registry_auth_after_release_failure(
    tmp_path: Path,
) -> None:
    # Reuse the success fixture's shape with a release script that fails after observing its config.
    # The separate subprocess keeps the failure path and the cleanup trap independently observable.
    sha = "a" * 40
    image = "ghcr.io/lejams/ari@sha256:" + "b" * 64
    bundle = tmp_path / "bundle"
    release = bundle / "deploy" / "release.sh"
    release.parent.mkdir(parents=True)
    release.write_text(
        "#!/usr/bin/env bash\n"
        "test -d \"$DOCKER_CONFIG\"\n"
        "printf '%s' \"$DOCKER_CONFIG\" > \"$RELEASE_CONFIG\"\n"
        "exit 7\n",
        encoding="utf-8",
    )
    (bundle / "release.json").write_text(
        f'{{"sha":"{sha}","image":"{image}"}}', encoding="utf-8"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "ssh").write_text(
        "#!/usr/bin/env bash\ncommand=\"${!#}\"\nbash -c \"$command\"\n", encoding="utf-8"
    )
    (fake_bin / "scp").write_text(
        "#!/usr/bin/env bash\n"
        "source=\"${@: -2:1}\"\n"
        "target=\"${!#}\"\n"
        "destination=\"${target#*:}\"\n"
        "mkdir -p \"$destination\"\n"
        "cp -R \"$source\"/. \"$destination\"/\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\n", encoding="utf-8"
    )
    for command in (fake_bin / "ssh", fake_bin / "scp", fake_bin / "docker"):
        command.chmod(0o755)
    release_config = tmp_path / "release-config"
    result = subprocess.run(
        [str(ROOT / "deploy/transfer-release.sh"), "production", str(bundle), image, sha],
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RELEASE_CONFIG": str(release_config),
            "DEPLOY_HOST": "release.example",
            "DEPLOY_USER": "ari",
            "DEPLOY_PATH": str(tmp_path / "remote"),
            "SSH_KNOWN_HOSTS": "release.example ssh-ed25519 AAAA",
            "SSH_PRIVATE_KEY": "private-key",
            "GHCR_USERNAME": "ari-actions",
            "GHCR_TOKEN": "fake-registry-token",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not Path(release_config.read_text(encoding="utf-8")).exists()
