"""Skill frameworks: validation, hashing and the strictness of loading."""

from __future__ import annotations

import copy

import pytest
from toy_domain import TOY_TAXONOMY, toy_framework

from ari.learning import FrameworkError, Skill, SkillEdge, SkillFramework


def skill(skill_id: str, half_life: float = 30.0) -> Skill:
    return Skill(skill_id, skill_id.upper(), "family", half_life)


def test_the_toy_framework_loads_and_answers_questions_about_itself() -> None:
    framework = toy_framework()
    assert framework.skill_ids == {
        "arithmetic",
        "fractions",
        "equations",
        "functions",
        "exam_rules",
    }
    assert framework.skill("exam_rules").critical is True
    assert framework.skill("fractions").critical is False
    assert [e.from_skill for e in framework.prerequisites_of("equations")] == ["fractions"]
    assert framework.prerequisites_of("arithmetic") == ()
    with pytest.raises(KeyError):
        framework.skill("absent")


def test_duplicate_ids_unknown_endpoints_self_loops_and_duplicate_edges_are_refused() -> None:
    with pytest.raises(FrameworkError, match="duplicate skill ids"):
        SkillFramework("f", "1", (skill("a"), skill("a")))
    with pytest.raises(FrameworkError, match="unknown skill"):
        SkillFramework("f", "1", (skill("a"),), (SkillEdge("a", "ghost", "prerequisite"),))
    with pytest.raises(FrameworkError, match="its own"):
        SkillFramework("f", "1", (skill("a"),), (SkillEdge("a", "a", "part_of"),))
    edge = SkillEdge("a", "b", "prerequisite")
    with pytest.raises(FrameworkError, match="duplicate"):
        SkillFramework("f", "1", (skill("a"), skill("b")), (edge, edge))
    with pytest.raises(FrameworkError, match="threshold"):
        SkillFramework(
            "f", "1", (skill("a"), skill("b")), (SkillEdge("a", "b", "prerequisite", 1.5),)
        )
    with pytest.raises(FrameworkError):
        SkillFramework("f", "1", ())
    with pytest.raises(ValueError):
        Skill("a", "A", "f", 0.0)


def test_cycles_are_refused_in_each_relation_and_named() -> None:
    skills = (skill("a"), skill("b"), skill("c"))
    ring = (
        SkillEdge("a", "b", "prerequisite"),
        SkillEdge("b", "c", "prerequisite"),
        SkillEdge("c", "a", "prerequisite"),
    )
    with pytest.raises(FrameworkError, match=r"prerequisite cycle: a -> b -> c -> a"):
        SkillFramework("f", "1", skills, ring)
    part_ring = tuple(SkillEdge(e.from_skill, e.to_skill, "part_of") for e in ring)
    with pytest.raises(FrameworkError, match="part_of cycle"):
        SkillFramework("f", "1", skills, part_ring)
    # The same pair in opposite relations is not a cycle: they are different graphs.
    SkillFramework(
        "f", "1", skills, (SkillEdge("a", "b", "prerequisite"), SkillEdge("b", "a", "part_of"))
    )


def test_cycle_detection_does_not_recurse_so_long_chains_are_fine() -> None:
    count = 5000
    skills = tuple(skill(f"s{i}") for i in range(count))
    chain = tuple(SkillEdge(f"s{i}", f"s{i + 1}", "prerequisite") for i in range(count - 1))
    SkillFramework("long", "1", skills, chain)
    with pytest.raises(FrameworkError, match="cycle"):
        SkillFramework(
            "long", "1", skills, (*chain, SkillEdge(f"s{count - 1}", "s0", "prerequisite"))
        )


def test_the_hash_ignores_order_and_changes_with_content() -> None:
    reference = toy_framework()
    shuffled = copy.deepcopy(TOY_TAXONOMY)
    shuffled["skills"].reverse()
    shuffled["edges"].reverse()
    assert SkillFramework.from_mapping(shuffled).content_hash == reference.content_hash
    assert len(reference.content_hash) == 64
    for change in (
        lambda d: d["skills"][0].update(half_life_days=61),
        lambda d: d["skills"][1].update(label="Fractions!"),
        lambda d: d["edges"][0].update(threshold=0.7),
        lambda d: d.update(version="2"),
        lambda d: d["skills"][4].update(critical=False),
    ):
        edited = copy.deepcopy(TOY_TAXONOMY)
        change(edited)
        assert SkillFramework.from_mapping(edited).content_hash != reference.content_hash


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda d: d.update(extra=1), "unknown key"),
        (lambda d: d["skills"][0].update(color="red"), "unknown key"),
        (lambda d: d["edges"][0].update(weight=1), "unknown key"),
        (lambda d: d.pop("skills"), "missing 'skills'"),
        (lambda d: d.update(skills="nope"), "list of mappings"),
        (lambda d: d["edges"][0].update(kind="requires"), "prerequisite or part_of"),
        (lambda d: d["skills"][0].update(half_life_days="30"), "must be a number"),
        (lambda d: d["skills"][0].update(half_life_days=True), "must be a number"),
        (lambda d: d["skills"][0].update(critical="yes"), "true or false"),
        (lambda d: d["skills"][0].update(half_life_days=-5), "positive"),
        (lambda d: d["edges"][0].update(to="ghost"), "unknown skill"),
    ],
)
def test_loading_refuses_what_it_does_not_recognise(edit, message: str) -> None:  # type: ignore[no-untyped-def]
    data = copy.deepcopy(TOY_TAXONOMY)
    edit(data)
    with pytest.raises(FrameworkError, match=message):
        SkillFramework.from_mapping(data)


def test_edges_are_optional() -> None:
    bare = {k: v for k, v in TOY_TAXONOMY.items() if k != "edges"}
    assert SkillFramework.from_mapping(bare).edges == ()
