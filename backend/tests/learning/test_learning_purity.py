"""The learning core is pure: standard library only, no I/O, no clock, no global randomness.

An import-linter contract forbids the known third-party libraries and every other ARI module;
it can only list what is forbidden. This test is the allow-list: it reads the source and
accepts only the standard-library modules named below, with no dynamic import. Adding a module
to the list is a deliberate, reviewed change.
"""

from __future__ import annotations

import ast
from pathlib import Path

import ari.learning

PACKAGE = Path(ari.learning.__file__).parent


def _sources(package: Path) -> list[Path]:
    """Every module of the package, sub-packages included: none escapes the checks."""
    return sorted(package.rglob("*.py"))


SOURCES = _sources(PACKAGE)

# The only modules the core may import: pure computation, no clock, disk, network or randomness.
ALLOWED_MODULES = {
    "__future__", "collections", "dataclasses", "datetime", "hashlib", "itertools", "json",
    "math", "typing", "typing_extensions",
}  # fmt: skip
CLOCK_ATTRIBUTES = {"now", "utcnow", "today", "fromtimestamp", "utcfromtimestamp"}
FORBIDDEN_CALLS = {"open", "print", "input", "exec", "eval", "compile", "__import__"}


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


def test_the_checks_reach_into_sub_packages(tmp_path: Path) -> None:
    nested = tmp_path / "learning" / "policy" / "scoring.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("import os\n", encoding="utf-8")
    (tmp_path / "learning" / "__init__.py").write_text("", encoding="utf-8")
    assert nested in _sources(tmp_path / "learning")


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
                assert root in ALLOWED_MODULES, (
                    f"{source.name} imports {module}, which is not on the allow-list"
                )


def test_no_io_no_clock_no_global_randomness() -> None:
    for source in SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_CALLS, f"{source.name} calls {node.func.id}()"
            # Whatever the owner is called (an alias, a dotted path): the core never reads time.
            if isinstance(node, ast.Attribute):
                assert node.attr not in CLOCK_ATTRIBUTES, (
                    f"{source.name} reads the clock with .{node.attr}"
                )
            if isinstance(node, ast.Name):
                assert node.id != "__import__", f"{source.name} uses __import__"
