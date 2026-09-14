"""Deterministic weighted v2 scoring; legacy scoring is deliberately not changed."""

import re

from ari.domain.models import AudioDeliveryStatus, ConversationSession, MedicalCase


def _phrase_present(phrase: str, text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    expected = " ".join(phrase.casefold().split())
    return re.search(r"(?<!\w)" + re.escape(expected) + r"(?!\w)", normalized) is not None


def weighted_assessment(session: ConversationSession, case: MedicalCase) -> list[dict[str, object]]:
    delivered = [
        t
        for t in session.turns
        if t.delivery_status is AudioDeliveryStatus.DELIVERED
        and not t.interrupted
        and t.provider_response_status == "completed"
    ]
    heard = {fact for turn in delivered for fact in turn.revealed_fact_ids}
    results: list[dict[str, object]] = []
    for dimension in case.rubric:
        items = [i for i in case.assessment_items if i.dimension == dimension.id]
        total_weight = sum(i.weight for i in items)
        earned = 0.0
        evidence: set[int] = set()
        missing_required = []
        details = []
        for item in items:
            if item.evidence_kind == "delivered_facts":
                checks = [fact in heard for fact in item.satisfied_by_fact_ids]
                item_evidence = {
                    t.sequence
                    for t in delivered
                    if set(t.revealed_fact_ids) & set(item.satisfied_by_fact_ids)
                }
            else:
                # Fact references provide context only; behavior requires the doctor's own text.
                checks = [
                    any(_phrase_present(p, t.user_text) for t in session.turns)
                    for p in item.doctor_phrases
                ]
                item_evidence = {
                    t.sequence
                    for t in session.turns
                    if any(_phrase_present(p, t.user_text) for p in item.doctor_phrases)
                }
            satisfied = bool(checks) and (
                all(checks) if item.satisfaction == "all" else any(checks)
            )
            if satisfied:
                earned += item.weight
                evidence.update(item_evidence)
            elif item.required:
                missing_required.append(item.id)
            details.append(
                {
                    "item_id": item.id,
                    "satisfied": satisfied,
                    "evidence_turn_sequences": sorted(item_evidence) if satisfied else [],
                }
            )
        score = dimension.max_score * earned / total_weight if total_weight else 0.0
        results.append(
            {
                "criterion_id": dimension.id,
                "score": round(score, 6),
                "evidence_turn_sequences": sorted(evidence),
                "feedback": f"Évaluation pondérée: {earned:g}/{total_weight:g} poids satisfaits.",
                "scoring_version": "assessment-weighted-v1",
                "assessment_items": details,
                "missing_required_item_ids": missing_required,
            }
        )
    return results
