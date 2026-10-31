#!/usr/bin/env python3
"""Validate and atomically install the three production provider secrets.

Values deliberately travel only through standard input/output; never accept them as arguments.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import NoReturn

SECRET_NAMES = ("ARI_OPENAI_API_KEY", "ARI_STT_API_KEY", "ARI_ELEVENLABS_API_KEY")
SECRET_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?(ARI_OPENAI_API_KEY|ARI_STT_API_KEY|ARI_ELEVENLABS_API_KEY)\s*="
)
TOKEN = re.compile(r"^[A-Za-z0-9_.:/+=-]+$")


def fail(message: str) -> NoReturn:
    print(f"provider secrets: {message}", file=sys.stderr)
    raise SystemExit(2)


def validate(values: Mapping[str, object]) -> dict[str, str]:
    if set(values) != set(SECRET_NAMES):
        fail("payload must contain exactly the required provider secret names")
    checked: dict[str, str] = {}
    for name in SECRET_NAMES:
        value = values[name]
        if not isinstance(value, str) or not TOKEN.fullmatch(value):
            fail(f"{name} must be a non-empty API token with safe characters")
        checked[name] = value
    return checked


def from_environment() -> dict[str, str]:
    return validate({name: os.environ.get(name, "") for name in SECRET_NAMES})


def from_stdin() -> dict[str, str]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        fail("payload is not valid JSON")
    if not isinstance(payload, dict):
        fail("payload must be an object")
    return validate(payload)


def install(env_file: Path, values: Mapping[str, str]) -> None:
    try:
        original = env_file.read_text(encoding="utf-8")
    except OSError:
        fail("cannot read environment file")
    retained = [
        line for line in original.splitlines(keepends=True)
        if not SECRET_ASSIGNMENT.match(line)
    ]
    if retained and not retained[-1].endswith("\n"):
        retained[-1] += "\n"
    replacement = "".join(retained) + "".join(
        f"{name}={values[name]}\n" for name in SECRET_NAMES
    )
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".provider-secrets.", dir=env_file.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write(replacement)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary_name, env_file)
    except OSError:
        with suppress(OSError):
            if temporary_name is not None:
                os.unlink(temporary_name)
        fail("cannot atomically update environment file")


def main(arguments: list[str]) -> None:
    if arguments == ["--validate-env"]:
        from_environment()
        return
    if arguments == ["--json-from-env"]:
        print(json.dumps(from_environment(), separators=(",", ":")))
        return
    if arguments == ["--normalize-stdin"]:
        print(json.dumps(from_stdin(), separators=(",", ":")))
        return
    if len(arguments) == 2 and arguments[0] == "--install-stdin":
        install(Path(arguments[1]), from_stdin())
        return
    fail(
        "usage: --validate-env | --json-from-env | --normalize-stdin "
        "| --install-stdin <environment-file>"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
