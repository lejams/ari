"""Focused safety tests for the keys-only production rotation runtime."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "ghcr.io/lejams/ari@sha256:" + "a" * 64
SHA = "b" * 40
PAYLOAD = json.dumps(
    {
        "ARI_OPENAI_API_KEY": "openai_key",
        "ARI_STT_API_KEY": "stt_key",
        "ARI_ELEVENLABS_API_KEY": "xi_key",
    }
)


def setup_rotation(tmp_path: Path) -> tuple[Path, Path, Path]:
    env = tmp_path / "ari"
    env.mkdir()
    release = env / "releases" / SHA
    release.mkdir(parents=True)
    (release / "docker-compose.yml").write_text("services: {}\n")
    (env / ".release.json").write_text(json.dumps({"sha": SHA, "image": IMAGE}))
    config = (
        "COMPOSE_PROJECT_NAME=ari\nUNRELATED=value\nARI_OPENAI_API_KEY=old\nARI_STT_API_KEY=old\n"
        "ARI_ELEVENLABS_API_KEY=old\n"
    )
    (env / ".env").write_text(config)
    (env / ".env").chmod(0o600)
    docker = tmp_path / "docker"
    docker.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]; open(os.environ['DOCKER_LOG'], 'a').write(' '.join(args) + '\\n')
image = os.environ['EXPECTED_IMAGE']
ids = {'app':'app-id','backoffice':'backoffice-id','worker':'worker-id'}
if args[:2] == ['compose', '-p'] and 'config' in args:
 if 'serve' not in args:
  print(json.dumps({'services': {}})); raise SystemExit
 image_value = 'wrong' if os.environ.get('BAD_CONFIG') else image
 environment = {
  'ARI_PATIENT_MODEL':'active-model',
  'ARI_OPENAI_API_KEY':'rendered',
  'LITERAL_DOLLAR':'single$$dollar',
  'DOUBLE_DOLLAR':'double$$$$dollar',
 }
 print(json.dumps({'services': {x:{'image':image_value, 'environment':environment} for x in ids}}))
elif args[:3] == ['image','inspect','--format']:
 if 'json .Config.Env' in args[3]: print(json.dumps(['PATH=/usr/local/bin', 'PYTHONUNBUFFERED=1']))
 else: print('sha256:recorded')
elif args[:2] == ['ps','-aq']:
 service = next(x for x in ids if any(arg.endswith('service='+x) for arg in args))
 print('' if os.environ.get('MISSING_CONTAINER') else ids[service])
elif args[:2] == ['ps','-q']:
 service = next(x for x in ids if any(arg.endswith('service='+x) for arg in args))
 print(ids[service])
elif args[:2] == ['inspect','--format']:
  ident = args[-1]; service = next(x for x,v in ids.items() if v == ident)
  if 'json .Config.Env' in args[2]:
   model = 'drifted-model' if os.environ.get('DRIFT_MODEL') else 'active-model'
   env = ['PATH=/usr/local/bin', 'PYTHONUNBUFFERED=1']
   env += ['ARI_PATIENT_MODEL='+model, 'ARI_OPENAI_API_KEY=old']
   env += ['LITERAL_DOLLAR=single$dollar', 'DOUBLE_DOLLAR=double$$dollar']
   print(json.dumps(env))
   raise SystemExit
  image_id = 'sha256:wrong' if os.environ.get('BAD_IMAGE') else 'sha256:recorded'
  if 'Labels' in args[2]: print(image_id+'|ari|'+service+'|true|healthy')
  else: print(image_id+'|true|healthy')
""")
    docker.chmod(0o755)
    return env, tmp_path, docker


def run(tmp_path: Path, env_dir: Path, payload: str = PAYLOAD) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DOCKER_LOG": str(tmp_path / "docker.log"),
        "EXPECTED_IMAGE": IMAGE,
    }
    return subprocess.run(
        [sys.executable, str(ROOT / "deploy/rotate-provider-keys.py"), str(env_dir)],
        input=payload,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


def test_rotation_recreates_only_consumers_and_preserves_metadata(tmp_path: Path) -> None:
    env, bin_dir, _ = setup_rotation(tmp_path)
    before = (env / ".release.json").read_bytes()
    result = run(bin_dir, env)
    assert result.returncode == 0, result.stderr
    assert (env / ".release.json").read_bytes() == before
    content = (env / ".env").read_text()
    assert (
        "UNRELATED=value" in content
        and "ARI_OPENAI_API_KEY=openai_key" in content
        and "ARI_STT_API_KEY=stt_key" in content
        and "ARI_ELEVENLABS_API_KEY=xi_key" in content
    )
    assert stat.S_IMODE((env / ".env").stat().st_mode) == 0o600
    commands = (bin_dir / "docker.log").read_text()
    assert (
        "up -d --no-build --no-deps --pull never --force-recreate "
        "--wait --wait-timeout 180 app backoffice worker" in commands
    )
    assert not any(
        word in commands
        for word in (" pull ", " build", " migrate", " postgres", " proxy", " volume")
    )


def test_invalid_metadata_rejects_before_docker_or_env_mutation(tmp_path: Path) -> None:
    env, bin_dir, _ = setup_rotation(tmp_path)
    (env / ".release.json").write_text("{}")
    before = (env / ".env").read_bytes()
    result = run(bin_dir, env)
    assert result.returncode != 0 and (env / ".env").read_bytes() == before
    assert not (bin_dir / "docker.log").exists()


def test_kernel_lock_conflict_rejects_before_mutation(tmp_path: Path) -> None:
    import fcntl

    env, bin_dir, _ = setup_rotation(tmp_path)
    before = (env / ".env").read_bytes()
    with (env / ".deploy.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run(bin_dir, env)
    assert result.returncode != 0 and (env / ".env").read_bytes() == before
    assert not (bin_dir / "docker.log").exists()


@pytest.mark.parametrize("failure", ["BAD_CONFIG", "MISSING_CONTAINER", "BAD_IMAGE"])
def test_active_release_mismatches_reject_before_key_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    env, bin_dir, _ = setup_rotation(tmp_path)
    before = (env / ".env").read_bytes()
    monkeypatch.setenv(failure, "1")
    result = run(bin_dir, env)
    assert result.returncode != 0
    assert (env / ".env").read_bytes() == before


def test_non_key_environment_drift_rejects_but_key_drift_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, bin_dir, _ = setup_rotation(tmp_path)
    before = (env / ".env").read_bytes()
    monkeypatch.setenv("DRIFT_MODEL", "1")
    drift = run(bin_dir, env)
    assert drift.returncode != 0
    assert (env / ".env").read_bytes() == before
    monkeypatch.delenv("DRIFT_MODEL")
    allowed = run(bin_dir, env)
    assert allowed.returncode == 0, allowed.stderr
    content = (env / ".env").read_text()
    assert "ARI_OPENAI_API_KEY=openai_key" in content
    assert "ARI_STT_API_KEY=stt_key" in content
    assert "ARI_ELEVENLABS_API_KEY=xi_key" in content
