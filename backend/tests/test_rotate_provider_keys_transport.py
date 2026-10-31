"""Transport tests: provider tokens travel only on the final SSH standard input."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPENAI = "transport_openai_key"
STT = "transport_stt_key"
ELEVENLABS = "transport_xi_key"


def write_executable(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(0o755)


def test_transport_uses_stdin_and_removes_private_files(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    remote = tmp_path / "remote"
    remote.mkdir()
    local_env = tmp_path / "environment"
    local_env.mkdir()
    log = tmp_path / "calls.log"
    write_executable(
        bin_dir / "ssh",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_LOG"
case "$*" in
  *mktemp*) mkdir -p "$FAKE_REMOTE"; echo /opt/ari/.provider-key-rotation.fake ;;
  *'rotate-provider-keys.py'*) exec python3 "$FAKE_REMOTE/rotate-provider-keys.py" "$FAKE_ENV" ;;
  *'rm -rf'*) rm -rf "$FAKE_REMOTE"/* ;;
esac
""",
    )
    write_executable(
        bin_dir / "scp",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_LOG"
for source in "$@"; do
  [[ -f "$source" ]] && cp "$source" "$FAKE_REMOTE/"
done
true
""",
    )
    write_executable(
        bin_dir / "docker",
        """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
image = os.environ['EXPECTED_IMAGE']
ids = {'app':'app', 'backoffice':'backoffice', 'worker':'worker'}
if 'config' in args:
 environment = {'ARI_PATIENT_MODEL':'active-model'}
 print(json.dumps({'services': {x:{'image':image, 'environment':environment} for x in ids}}))
elif args[:2] == ['image', 'inspect']:
 if 'json .Config.Env' in args[3]: print(json.dumps(['PATH=/usr/local/bin']))
 else: print('sha256:recorded')
elif args[:2] == ['ps', '-aq'] or args[:2] == ['ps', '-q']:
 print(ids[next(x for x in ids if any(a.endswith('service='+x) for a in args))])
elif args[:1] == ['inspect']:
 service = args[-1]
 if 'json .Config.Env' in args[2]:
  print(json.dumps(['PATH=/usr/local/bin', 'ARI_PATIENT_MODEL=active-model']))
 elif 'Labels' in args[2]: print('sha256:recorded|ari|'+service+'|true|healthy')
 else: print('sha256:recorded|true|healthy')
""",
    )
    image = "ghcr.io/lejams/ari@sha256:" + "a" * 64
    sha = "b" * 40
    release = local_env / "releases" / sha
    release.mkdir(parents=True)
    (release / "docker-compose.yml").write_text("services: {}\n")
    (local_env / ".release.json").write_text(f'{{"sha":"{sha}","image":"{image}"}}')
    env_file = local_env / ".env"
    env_file.write_text("COMPOSE_PROJECT_NAME=ari\nUNCHANGED=yes\n")
    env_file.chmod(0o600)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TMPDIR": str(tmp_path / "ssh-temp"),
        "FAKE_LOG": str(log),
        "FAKE_REMOTE": str(remote),
        "FAKE_ENV": str(local_env),
        "EXPECTED_IMAGE": image,
        "DEPLOY_HOST": "example.test",
        "DEPLOY_USER": "ari",
        "DEPLOY_PORT": "22",
        "DEPLOY_PATH": "/opt/ari",
        "SSH_KNOWN_HOSTS": "known-host",
        "SSH_PRIVATE_KEY": "private-key",
        "ARI_OPENAI_API_KEY": OPENAI,
        "ARI_STT_API_KEY": STT,
        "ARI_ELEVENLABS_API_KEY": ELEVENLABS,
    }
    Path(env["TMPDIR"]).mkdir()
    result = subprocess.run(
        [str(ROOT / "deploy/rotate-provider-keys.sh")],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert f"ARI_OPENAI_API_KEY={OPENAI}" in env_file.read_text()
    assert f"ARI_STT_API_KEY={STT}" in env_file.read_text()
    assert f"ARI_ELEVENLABS_API_KEY={ELEVENLABS}" in env_file.read_text()
    transcript = log.read_text() + result.stdout + result.stderr
    assert ELEVENLABS not in transcript
    assert OPENAI not in transcript and STT not in transcript
    assert list(remote.iterdir()) == []
    assert list(Path(env["TMPDIR"]).iterdir()) == []


def test_missing_key_and_unsafe_host_fail_before_ssh(tmp_path: Path) -> None:
    marker = tmp_path / "ssh-called"
    write_executable(tmp_path / "ssh", f"#!/usr/bin/env bash\ntouch {marker}\n")
    base = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DEPLOY_HOST": "bad;host",
        "DEPLOY_USER": "ari",
        "DEPLOY_PATH": "/opt/ari",
        "SSH_KNOWN_HOSTS": "known",
        "SSH_PRIVATE_KEY": "key",
        "ARI_OPENAI_API_KEY": OPENAI,
        "ARI_STT_API_KEY": STT,
        "ARI_ELEVENLABS_API_KEY": ELEVENLABS,
    }
    unsafe = subprocess.run([str(ROOT / "deploy/rotate-provider-keys.sh")], env=base, check=False)
    assert unsafe.returncode != 0 and not marker.exists()
    missing = base | {"DEPLOY_HOST": "example.test", "ARI_OPENAI_API_KEY": ""}
    missing_result = subprocess.run(
        [str(ROOT / "deploy/rotate-provider-keys.sh")], env=missing, check=False
    )
    assert missing_result.returncode != 0 and not marker.exists()
