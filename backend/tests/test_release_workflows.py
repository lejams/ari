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
    assert jobs["staging"]["concurrency"]["cancel-in-progress"] == "false"
    assert "staged-release" in str(jobs["staging"])
    assert "include-hidden-files" in str(jobs["staging"])

def test_promotion_is_manual_main_only_and_validates_staging_proof() -> None:
    workflow = load_workflow(".github/workflows/promote.yml")
    assert set(workflow["on"]) == {"workflow_dispatch"}
    production = workflow["jobs"]["production"]
    assert production["environment"] == "production"
    assert "refs/heads/main" in production["if"]
    steps = str(production["steps"])
    assert "actions/runs/$RUN_ID" in steps
    assert "workflow_id" in steps
    assert "merge-base --is-ancestor" in steps
    assert "staging-success.json" in steps
    assert "workflow_run" not in steps

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
