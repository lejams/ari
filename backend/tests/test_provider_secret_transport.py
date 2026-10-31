from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("registry_auth", [False, True])
def test_secret_transport_keeps_registry_and_provider_streams_separate(
    tmp_path: Path, registry_auth: bool,
) -> None:
    sha = "a" * 40
    image = "ghcr.io/lejams/ari@sha256:" + "b" * 64
    bundle = tmp_path / "bundle"
    scripts = bundle / "deploy"
    scripts.mkdir(parents=True)
    shutil.copy(ROOT / "deploy/provider_secrets.py", scripts / "provider_secrets.py")
    (bundle / "release.json").write_text(json.dumps({"sha": sha, "image": image}))
    (scripts / "release.sh").write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, subprocess, sys\n"
        "assert sys.argv[5] == '--provider-secrets-stdin'\n"
        "pathlib.Path(os.environ['CONFIG_CAPTURE']).write_text(os.getenv('DOCKER_CONFIG', ''))\n"
        "helper = pathlib.Path(__file__).with_name('provider_secrets.py')\n"
        "subprocess.run([sys.executable, str(helper), '--install-stdin',\n"
        "                str(pathlib.Path(sys.argv[2]) / '.env')], check=True)\n"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "ssh").write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, subprocess, sys\n"
        "with pathlib.Path(os.environ['SSH_LOG']).open('a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "target = sys.argv.index('ari@release.example')\n"
        "sys.exit(subprocess.run(['bash', '-c', ' '.join(sys.argv[target + 1:])]).returncode)\n"
    )
    (fake_bin / "scp").write_text(
        f"#!{sys.executable}\n"
        "import pathlib, shutil, sys\n"
        "destination = pathlib.Path(sys.argv[-1].split(':', 1)[1])\n"
        "shutil.copytree(sys.argv[-2], destination, dirs_exist_ok=True)\n"
    )
    (fake_bin / "docker").write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "assert sys.argv[1:] == ['login', 'ghcr.io', '--username',\n"
        "                        'ari-actions', '--password-stdin']\n"
        "pathlib.Path(os.environ['TOKEN_CAPTURE']).write_text(sys.stdin.read())\n"
    )
    for script in fake_bin.iterdir():
        script.chmod(0o755)
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / ".env").write_text("OTHER=preserved\n")
    ssh_log = tmp_path / "ssh.log"
    token_capture = tmp_path / "token"
    config_capture = tmp_path / "config"
    values = {
        "ARI_OPENAI_API_KEY": "sk-test-unique",
        "ARI_STT_API_KEY": "gsk_test-unique",
        "ARI_ELEVENLABS_API_KEY": "xi_test-unique",
    }
    environment = os.environ | values | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DEPLOY_HOST": "release.example",
        "DEPLOY_USER": "ari",
        "DEPLOY_PATH": str(remote),
        "SSH_KNOWN_HOSTS": "release.example ssh-ed25519 AAAA",
        "SSH_PRIVATE_KEY": "fake-private-key",
        "ARI_SYNC_PROVIDER_SECRETS": "1",
        "GHCR_USERNAME": "ari-actions" if registry_auth else "",
        "GHCR_TOKEN": "registry-unique-token" if registry_auth else "",
        "TOKEN_CAPTURE": str(token_capture),
        "CONFIG_CAPTURE": str(config_capture),
        "SSH_LOG": str(ssh_log),
    }
    result = subprocess.run(
        [str(ROOT / "deploy/transfer-release.sh"), "production", str(bundle), image, sha],
        env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    contents = (remote / ".env").read_text()
    assert contents.startswith("OTHER=preserved\n")
    for name, value in values.items():
        assert f"{name}={value}\n" in contents
    if registry_auth:
        assert token_capture.read_text() == environment["GHCR_TOKEN"]
        assert not Path(config_capture.read_text()).exists()
    else:
        assert not token_capture.exists()
        assert config_capture.read_text() == ""
    public_output = result.stdout + result.stderr + ssh_log.read_text()
    bundle_files = [file for file in bundle.rglob("*") if file.is_file()]
    bundle_files += [file for file in (remote / "releases").rglob("*") if file.is_file()]
    for secret in [*values.values(), "registry-unique-token"]:
        assert secret not in public_output
        assert all(secret not in file.read_text() for file in bundle_files)


@pytest.mark.parametrize("value", ["~", "foo:~", "quote'", "back\\slash", "$HOME", "with space"])
def test_unsafe_provider_token_is_rejected_without_mutation(tmp_path: Path, value: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=preserved\n")
    result = subprocess.run(
        [sys.executable, str(ROOT / "deploy/provider_secrets.py"),
         "--install-stdin", str(env_file)],
        input=json.dumps(
            {
                "ARI_OPENAI_API_KEY": value,
                "ARI_STT_API_KEY": "safe",
                "ARI_ELEVENLABS_API_KEY": "safe",
            }
        ),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert env_file.read_text() == "OTHER=preserved\n"
    assert value not in result.stdout + result.stderr
