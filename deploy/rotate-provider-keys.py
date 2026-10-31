"""Rotate only the three provider keys for the currently active production image."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

from provider_secrets import from_stdin, install

SHA = re.compile(r"^[0-9a-f]{40}$")
IMAGE = re.compile(r"^ghcr\.io/lejams/ari@sha256:[0-9a-f]{64}$")
SERVICES = ("app", "backoffice", "worker")
PROVIDER_KEYS = ("ARI_OPENAI_API_KEY", "ARI_STT_API_KEY", "ARI_ELEVENLABS_API_KEY")


def fail(message: str) -> NoReturn:
    print(f"provider key rotation: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str], *, env: dict[str, str]) -> str:
    result = subprocess.run(
        command, text=True, capture_output=True, env=env, check=False
    )
    if result.returncode:
        fail("Docker verification or service restart failed")
    return result.stdout


def non_provider_environment(environment: object) -> dict[str, str]:
    if not isinstance(environment, list) or not all(
        isinstance(item, str) for item in environment
    ):
        fail("active service configuration is invalid")
    values: dict[str, str] = {}
    for item in environment:
        if "=" not in item:
            fail("active service configuration is invalid")
        name, value = item.split("=", 1)
        if not name or name in values:
            fail("active service configuration is invalid")
        if name not in PROVIDER_KEYS:
            values[name] = value
    return values


def rendered_environment(
    service: object, image_defaults: dict[str, str]
) -> dict[str, str]:
    if not isinstance(service, dict) or not isinstance(
        service.get("environment"), dict
    ):
        fail("active compose file is malformed")
    values = service["environment"]
    if not all(
        isinstance(name, str) and isinstance(value, str)
        for name, value in values.items()
    ):
        fail("active compose file is malformed")
    return image_defaults | non_provider_environment(
        [f"{name}={value.replace('$$', '$')}" for name, value in values.items()]
    )


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: rotate-provider-keys.py <environment-dir>")
    env_dir = Path(sys.argv[1])
    if not env_dir.is_absolute() or not env_dir.is_dir():
        fail("invalid rotation runtime paths")

    # Consume and validate stdin before invoking Docker: no child command can receive credentials.
    values = from_stdin()

    lock = (env_dir / ".deploy.lock").open("a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        fail("another deployment or live restore holds the deployment lock")
    env_file = env_dir / ".env"
    metadata = env_dir / ".release.json"
    if not env_file.is_file() or env_file.stat().st_mode & 0o077:
        fail("environment file must exist and be private")
    try:
        release = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fail("active release metadata is missing or malformed")
    if not isinstance(release, dict) or set(release) != {"sha", "image"}:
        fail("active release metadata is malformed")
    sha, image = release["sha"], release["image"]
    if (
        not isinstance(sha, str)
        or not SHA.fullmatch(sha)
        or not isinstance(image, str)
        or not IMAGE.fullmatch(image)
    ):
        fail("active release metadata is malformed")
    if (
        "COMPOSE_PROJECT_NAME=ari"
        not in env_file.read_text(encoding="utf-8").splitlines()
    ):
        fail("environment must select the ari project")
    releases = env_dir / "releases"
    release_dir = releases / sha
    active = release_dir / "docker-compose.yml"
    if (
        not active.is_file()
        or active.is_symlink()
        or release_dir.is_symlink()
        or not releases.is_dir()
    ):
        fail("active release bundle is missing or unsafe")
    try:
        active.resolve().relative_to(releases.resolve())
    except ValueError:
        fail("active release bundle is unsafe")

    child_env = os.environ.copy()
    child_env["ARI_IMAGE_REF"] = image
    for key in PROVIDER_KEYS:
        child_env.pop(key, None)
    compose = [
        "docker",
        "compose",
        "-p",
        "ari",
        "--profile",
        "serve",
        "--env-file",
        str(env_file),
        "-f",
        str(active),
    ]
    # All active-state checks happen while holding the same lock as releases/restores.
    try:
        rendered = json.loads(
            run(compose + ["config", "--format", "json"], env=child_env)
        )
        rendered_services = rendered["services"]
    except (ValueError, KeyError, TypeError):
        fail("active compose file is malformed")
    if not isinstance(rendered_services, dict):
        fail("active compose file is malformed")
    for service in SERVICES:
        if (
            not isinstance(rendered_services.get(service), dict)
            or rendered_services[service].get("image") != image
        ):
            fail("active compose file does not render the recorded image")
    recorded_id = run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image], env=child_env
    ).strip()
    if not recorded_id:
        fail("recorded image is not available locally")
    try:
        raw_image_environment = json.loads(
            run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{json .Config.Env}}",
                    image,
                ],
                env=child_env,
            )
        )
    except ValueError:
        fail("recorded image configuration is invalid")
    image_environment = non_provider_environment(raw_image_environment or [])
    for service in SERVICES:
        containers = run(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=ari",
                "--filter",
                f"label=com.docker.compose.service={service}",
            ],
            env=child_env,
        ).splitlines()
        if len(containers) != 1:
            fail("active service inventory is invalid")
        details = (
            run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{.Image}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Running}}|{{.State.Health.Status}}',
                    containers[0],
                ],
                env=child_env,
            )
            .strip()
            .split("|")
        )
        if details != [recorded_id, "ari", service, "true", "healthy"]:
            fail("active service does not match the recorded release")
        try:
            actual_environment = non_provider_environment(
                json.loads(
                    run(
                        [
                            "docker",
                            "inspect",
                            "--format",
                            "{{json .Config.Env}}",
                            containers[0],
                        ],
                        env=child_env,
                    )
                )
            )
        except ValueError:
            fail("active service configuration is invalid")
        if actual_environment != rendered_environment(
            rendered_services[service], image_environment
        ):
            fail("active service configuration does not match the recorded release")
    # Mutation is deliberately limited to the three entries and the three consumers.
    install(env_file, values)
    run(
        compose
        + [
            "up",
            "-d",
            "--no-build",
            "--no-deps",
            "--pull",
            "never",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "180",
            *SERVICES,
        ],
        env=child_env,
    )
    for service in SERVICES:
        container = run(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                "label=com.docker.compose.project=ari",
                "--filter",
                f"label=com.docker.compose.service={service}",
            ],
            env=child_env,
        ).strip()
        details = (
            run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.Image}}|{{.State.Running}}|{{.State.Health.Status}}",
                    container,
                ],
                env=child_env,
            )
            .strip()
            .split("|")
        )
        if details != [recorded_id, "true", "healthy"]:
            fail("recreated service does not use the recorded image")
    lock.close()
    print(
        f"Rotated provider keys for active release {sha} ({image}); app, backoffice and worker are running."
    )


if __name__ == "__main__":
    main()
