"""Three concrete next steps per session, from deterministic signals in priority order.

1. a required assessment item the learner missed;
2. an anamnesis section left uncovered;
3. an empathy moment the learner ignored or only partly acknowledged;
4. words added to the lexicon to review.
The LLM never chooses these; it only supplied the empathy verdicts with evidence.
"""

from typing import Any, cast

from ari.domain.models import MedicalCase

MAX_ACTIONS = 3
NEXT_ACTIONS_VERSION = "next-actions-v1"


def next_actions(
    case: MedicalCase,
    criteria: list[dict[str, object]],
    structure: dict[str, Any] | None,
    empathy: tuple[dict[str, Any], ...],
    lexicon_candidates: int,
) -> tuple[dict[str, Any], ...]:
    actions: list[dict[str, Any]] = []
    labels = {item.id: item.label for item in case.assessment_items}
    for criterion in criteria:
        missing_ids = cast(list[str], criterion.get("missing_required_item_ids", []))
        for item_id in missing_ids:
            actions.append(
                {
                    "kind": "missing_required_item",
                    "text": f"Obtenez ce qui manquait : {labels.get(str(item_id), str(item_id))}.",
                    "target": {"kind": "assessment_item", "id": str(item_id)},
                }
            )
    if structure is not None:
        for section in structure["sections"]:
            if section["missing_fact_ids"]:
                missing = len(section["missing_fact_ids"])
                actions.append(
                    {
                        "kind": "uncovered_section",
                        "text": (
                            f"Couvrez la section « {section['label']} » : "
                            f"{missing} information{'s' if missing > 1 else ''} non recueillie"
                            f"{'s' if missing > 1 else ''}."
                        ),
                        "target": {"kind": "section", "id": section["id"]},
                    }
                )
    for moment in empathy:
        if moment["verdict"] in {"ignored", "partial"}:
            actions.append(
                {
                    "kind": "empathy",
                    "text": (
                        f"Au tour {moment['response_turn']}, réagissez d'abord au vécu du patient "
                        f"({moment['cue']}) : {moment['expected']}"
                    ),
                    "target": {"kind": "empathy_moment", "id": moment["moment_id"]},
                }
            )
    if lexicon_candidates:
        actions.append(
            {
                "kind": "review_lexicon",
                "text": (
                    f"Révisez les {lexicon_candidates} mot{'s' if lexicon_candidates > 1 else ''} "
                    "repérés dans votre carnet avant la prochaine session."
                ),
                "target": {"kind": "view", "id": "vocab"},
            }
        )
    return tuple(actions[:MAX_ACTIONS])
