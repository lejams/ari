"""Field-level differences between two versions of a record, on their canonical JSON dumps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class FieldChange:
    path: str
    before: Any
    after: Any
    kind: Literal["added", "removed", "changed"]


def field_diff(before: Any, after: Any, path: str = "") -> tuple[FieldChange, ...]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[FieldChange] = []
        for key in sorted(set(before) | set(after), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in before:
                changes.append(FieldChange(child, None, after[key], "added"))
            elif key not in after:
                changes.append(FieldChange(child, before[key], None, "removed"))
            else:
                changes.extend(field_diff(before[key], after[key], child))
        return tuple(changes)
    if isinstance(before, list) and isinstance(after, list):
        changes = []
        for index in range(max(len(before), len(after))):
            child = f"{path}[{index}]"
            if index >= len(before):
                changes.append(FieldChange(child, None, after[index], "added"))
            elif index >= len(after):
                changes.append(FieldChange(child, before[index], None, "removed"))
            else:
                changes.extend(field_diff(before[index], after[index], child))
        return tuple(changes)
    if before != after:
        return (FieldChange(path or "$", before, after, "changed"),)
    return ()
