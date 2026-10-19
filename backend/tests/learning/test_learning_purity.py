"""The learning core is pure: standard library only, no I/O, no clock, no global randomness.

An import-linter contract forbids the known third-party libraries and every other ARI module;
this test is the guarantee for everything the contract cannot list, and it reads the source.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import ari.learning

PACKAGE = Path(ari.learning.__file__).parent
SOURCES = sorted(PACKAGE.glob("*.py"))

ALLOWED_THIRD_PARTY = {"typing_extensions"}
# Standard-library modules that read the clock, the disk, the network or a global generator.
FORBIDDEN_STDLIB = {
    "os", "pathlib", "io", "socket", "subprocess", "shutil", "tempfile", "random", "secrets",
    "time", "sched", "threading", "asyncio", "urllib", "http", "sqlite3", "logging", "sys",
}  # fmt: skip
CLOCK_ATTRIBUTES = {"now", "utcnow", "today", "fromtimestamp"}


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, 0) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "", node.level))
    return found


def test_the_package_has_the_expected_modules() -> None:
    assert {p.name for p in SOURCES} >= {
        "__init__.py", "beta.py", "clock.py", "framework.py", "mastery.py", "model.py", "replay.py"
    }  # fmt: skip


def test_only_the_standard_library_and_the_package_itself_are_imported() -> None:
    for source in SOURCES:
        for module, level in _imports(ast.parse(source.read_text(encoding="utf-8"))):
            if level:  # a relative import stays inside the package
                continue
            root = module.split(".")[0]
            if root == "ari":
                assert module == "ari.learning" or module.startswith("ari.learning."), (
                    f"{source.name} imports {module}: the core knows nothing of the rest of ARI"
                )
            else:
                assert root in sys.stdlib_module_names or root in ALLOWED_THIRD_PARTY, (
                    f"{source.name} imports {module}, which is not the standard library"
                )


def test_no_io_no_clock_no_global_randomness() -> None:
    for source in SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for module, level in _imports(tree):
            assert level or module.split(".")[0] not in FORBIDDEN_STDLIB, (
                f"{source.name} imports {module}: inject it instead"
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "print", "input", "exec", "eval"}, (
                    f"{source.name} calls {node.func.id}()"
                )
            if isinstance(node, ast.Attribute) and node.attr in CLOCK_ATTRIBUTES:
                owner = node.value
                assert not (
                    isinstance(owner, ast.Name) and owner.id in {"datetime", "date", "time"}
                ), f"{source.name} reads the clock with {owner.id}.{node.attr}"
