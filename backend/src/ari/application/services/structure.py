"""Anamnesis structure: which authored sections the learner covered, and in which order.

Deterministic, computed from delivered facts exactly like the weighted assessment. It is
a checklist, not a judgement of clinical reasoning.
"""

from typing import Any

from ari.domain.clinical import ANAMNESIS_SECTION_IDS
from ari.domain.models import AudioDeliveryStatus, ConversationSession, MedicalCase

STRUCTURE_VERSION = "anamnesis-sections-v1"


def delivered_turns(session: ConversationSession) -> list[Any]:
    return [
        turn
        for turn in session.turns
        if turn.delivery_status is AudioDeliveryStatus.DELIVERED
        and turn.provider_response_status == "completed"
    ]


def first_delivery_turn(session: ConversationSession) -> dict[str, int]:
    """Fact id -> sequence of the first turn where the learner heard it."""
    first: dict[str, int] = {}
    for turn in delivered_turns(session):
        for fact_id in turn.revealed_fact_ids:
            first.setdefault(fact_id, turn.sequence)
    return first


def section_coverage(session: ConversationSession, case: MedicalCase) -> dict[str, Any] | None:
    if not case.anamnesis_sections:
        return None
    heard = first_delivery_turn(session)
    sections: list[dict[str, Any]] = []
    first_turns: dict[str, int] = {}
    for section in case.anamnesis_sections:
        covered = [f for f in section.fact_ids if f in heard]
        if covered:
            first_turns[section.id] = min(heard[f] for f in covered)
        sections.append(
            {
                "id": section.id,
                "label": section.label,
                "fact_ids": list(section.fact_ids),
                "covered_fact_ids": covered,
                "missing_fact_ids": [f for f in section.fact_ids if f not in heard],
                "first_turn": first_turns.get(section.id),
            }
        )
    order_observed = sorted(first_turns, key=lambda section_id: first_turns[section_id])
    canonical_index = [ANAMNESIS_SECTION_IDS.index(s) for s in order_observed]
    return {
        "version": STRUCTURE_VERSION,
        "sections": sections,
        "order_observed": order_observed,
        "canonical_order_respected": canonical_index == sorted(canonical_index),
        "covered_count": sum(1 for s in sections if not s["missing_fact_ids"]),
        "total_count": len(sections),
    }
