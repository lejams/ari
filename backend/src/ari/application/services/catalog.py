"""Catalogue views derived from published cases; pure functions, nothing stored."""

from collections.abc import Iterable
from typing import Any

from ari.domain.geography import Land
from ari.domain.models import MedicalCase


def land_summary(
    cases: Iterable[MedicalCase], worked_case_ids: Iterable[str], learner_land: Land | None
) -> dict[str, Any]:
    """Published cases per Land and how many of them this learner already worked.

    Only Länder with at least one published case appear, so the learner sees where the
    bank is thin instead of a list of sixteen zeros.
    """
    worked = set(worked_case_ids)
    per_land: dict[Land, list[MedicalCase]] = {}
    without_land = 0
    for case in cases:
        if not case.available_for_new_sessions:
            continue
        if case.land is None:
            without_land += 1
            continue
        per_land.setdefault(case.land, []).append(case)
    laender = []
    for land in sorted(per_land, key=lambda item: item.value):
        items = per_land[land]
        done = sum(1 for case in items if case.id in worked)
        laender.append(
            {
                "land": land.value,
                "cases": len(items),
                "worked": done,
                "share_worked": round(done / len(items), 2),
            }
        )
    return {
        "learner_land": learner_land.value if learner_land else None,
        "laender": laender,
        "without_land": without_land,
    }
