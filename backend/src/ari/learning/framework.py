"""A skill framework: the skills of a domain and how they depend on one another.

The core does not read files. The application parses a YAML taxonomy and hands over a mapping;
`SkillFramework.from_mapping` validates it (unique ids, edges between known skills, no cycle in
either relation) and gives it a content hash, so a mastery profile can name the exact taxonomy
it was computed against.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from ari.learning.model import EdgeKind, Skill, SkillEdge

_SKILL_KEYS = frozenset({"id", "label", "family", "half_life_days", "critical"})
_EDGE_KEYS = frozenset({"from", "to", "kind", "threshold"})
_FRAMEWORK_KEYS = frozenset({"id", "version", "skills", "edges"})
_EDGE_KINDS = frozenset({"prerequisite", "part_of"})


class FrameworkError(ValueError):
    """The taxonomy is not a valid skill framework."""


def _find_cycle(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> list[str] | None:
    """A cycle in the directed graph as a list of nodes, or None. Iterative: no recursion limit."""
    successors: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        successors[source].append(target)
    state: dict[str, int] = {}  # 1 = on the current path, 2 = fully explored
    for root in sorted(nodes):
        if state.get(root):
            continue
        path = [root]
        iterators = [iter(sorted(successors[root]))]
        state[root] = 1
        while path:
            for follower in iterators[-1]:
                if state.get(follower) == 1:
                    return [*path[path.index(follower) :], follower]
                if not state.get(follower):
                    state[follower] = 1
                    path.append(follower)
                    iterators.append(iter(sorted(successors[follower])))
                    break
            else:
                state[path.pop()] = 2
                iterators.pop()
    return None


@dataclass(frozen=True, slots=True)
class SkillFramework:
    id: str
    version: str
    skills: tuple[Skill, ...]
    edges: tuple[SkillEdge, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.version:
            raise FrameworkError("a framework needs an id and a version")
        if not self.skills:
            raise FrameworkError("a framework needs at least one skill")
        ids = [skill.id for skill in self.skills]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise FrameworkError(f"duplicate skill ids: {duplicates}")
        known = set(ids)
        seen: set[tuple[str, str, str]] = set()
        for edge in self.edges:
            if edge.from_skill not in known or edge.to_skill not in known:
                raise FrameworkError(
                    f"edge {edge.from_skill} -> {edge.to_skill} names an unknown skill"
                )
            if edge.from_skill == edge.to_skill:
                raise FrameworkError(f"a skill cannot be its own {edge.kind}: {edge.from_skill}")
            if not 0.0 <= edge.threshold <= 1.0:
                raise FrameworkError(f"edge threshold lies in [0, 1]: {edge}")
            key = (edge.kind, edge.from_skill, edge.to_skill)
            if key in seen:
                raise FrameworkError(
                    f"duplicate {edge.kind} edge {edge.from_skill} -> {edge.to_skill}"
                )
            seen.add(key)
        for kind in sorted(_EDGE_KINDS):
            cycle = _find_cycle(
                known, ((e.from_skill, e.to_skill) for e in self.edges if e.kind == kind)
            )
            if cycle is not None:
                raise FrameworkError(f"{kind} cycle: {' -> '.join(cycle)}")

    @property
    def skill_ids(self) -> frozenset[str]:
        return frozenset(skill.id for skill in self.skills)

    def skill(self, skill_id: str) -> Skill:
        for skill in self.skills:
            if skill.id == skill_id:
                return skill
        raise KeyError(skill_id)

    def prerequisites_of(self, skill_id: str) -> tuple[SkillEdge, ...]:
        return tuple(e for e in self.edges if e.kind == "prerequisite" and e.to_skill == skill_id)

    @property
    def content_hash(self) -> str:
        """SHA-256 of a canonical form: independent of the order skills and edges are listed in."""
        canonical = {
            "id": self.id,
            "version": self.version,
            "skills": [
                {
                    "id": s.id,
                    "label": s.label,
                    "family": s.family,
                    "half_life_days": float(s.half_life_days),
                    "critical": s.critical,
                }
                for s in sorted(self.skills, key=lambda s: s.id)
            ],
            "edges": [
                {
                    "from": e.from_skill,
                    "to": e.to_skill,
                    "kind": e.kind,
                    "threshold": float(e.threshold),
                }
                for e in sorted(self.edges, key=lambda e: (e.kind, e.from_skill, e.to_skill))
            ],
        }
        text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SkillFramework:
        """Build a framework from a parsed taxonomy, refusing anything it does not recognise."""
        _no_extra(data, _FRAMEWORK_KEYS, "framework")
        skills = tuple(_skill(raw) for raw in _sequence(data, "skills"))
        edges = tuple(_edge(raw) for raw in _sequence(data, "edges", required=False))
        return cls(
            _text(data, "id", "framework"), _text(data, "version", "framework"), skills, edges
        )


def _no_extra(data: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        raise FrameworkError(f"unknown key(s) in {where}: {extra}")


def _sequence(
    data: Mapping[str, Any], key: str, *, required: bool = True
) -> list[Mapping[str, Any]]:
    value = data.get(key)
    if value is None:
        if required:
            raise FrameworkError(f"missing '{key}'")
        return []
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise FrameworkError(f"'{key}' must be a list of mappings")
    return cast(list[Mapping[str, Any]], value)


def _text(data: Mapping[str, Any], key: str, where: str, *, required: bool = True) -> str:
    value = data.get(key, None if required else "")
    if not isinstance(value, str) or (required and not value):
        raise FrameworkError(f"{where}: '{key}' must be a non-empty string")
    return value


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FrameworkError(f"{where} must be a number")
    return float(value)


def _skill(raw: Mapping[str, Any]) -> Skill:
    _no_extra(raw, _SKILL_KEYS, "skill")
    where = f"skill {raw.get('id', '?')}"
    critical = raw.get("critical", False)
    if not isinstance(critical, bool):
        raise FrameworkError(f"{where}: critical must be true or false")
    try:
        return Skill(
            id=_text(raw, "id", where),
            label=_text(raw, "label", where, required=False),
            family=_text(raw, "family", where),
            half_life_days=_number(raw.get("half_life_days"), f"{where}: half_life_days"),
            critical=critical,
        )
    except ValueError as exc:
        raise FrameworkError(str(exc)) from exc


def _edge(raw: Mapping[str, Any]) -> SkillEdge:
    _no_extra(raw, _EDGE_KEYS, "edge")
    kind = raw.get("kind")
    if kind not in _EDGE_KINDS:
        raise FrameworkError(f"edge kind must be prerequisite or part_of, not {kind!r}")
    return SkillEdge(
        from_skill=_text(raw, "from", "edge"),
        to_skill=_text(raw, "to", "edge"),
        kind=cast(EdgeKind, kind),
        threshold=_number(raw.get("threshold", 0.5), "edge threshold"),
    )
