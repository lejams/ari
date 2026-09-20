"""Deterministic stand-ins for the content prompts, registered on the fake LLM provider.

They read the same JSON payload the real prompts receive and derive their answer from simple
text markers, so the pipeline runs end to end offline with predictable results.
"""

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from ari.content.schemas import BundleDraftOutput, ExtractionOutput, SegmentationOutput
from ari.domain.geography import Land

PROTOCOL_MARKER = "Protokoll"
LAND_LINE = re.compile(r"Land:\s*([A-Za-zÄÖÜäöüß-]+)")
CITY_LINE = re.compile(r"Stadt:\s*([A-Za-zÄÖÜäöüß-]+)")
MONTH_LINE = re.compile(r"Datum:\s*(\d{4}-\d{2})")
AGE = re.compile(r"(\d{1,3}) Jahre")
COMPLAINT = re.compile(r"Beschwerden:\s*(.+)")
PAGE_MARKER = re.compile(r"<<<SEITE (\d+)>>>\n?")


def _pages(text: str) -> list[tuple[int, str]]:
    parts = PAGE_MARKER.split(text)
    # parts = [before, n1, text1, n2, text2, ...]
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def _land(text: str) -> str | None:
    match = LAND_LINE.search(text)
    if match and match.group(1) in {land.value for land in Land}:
        return match.group(1)
    return None


def segmentation(payload: dict[str, Any]) -> dict[str, Any]:
    """One span per page that carries the protocol marker, from the window's own pages on."""
    own_from = int(payload.get("own_pages_from", 1))
    spans = []
    unassigned = []
    for number, text in _pages(str(payload["text"])):
        if number < own_from:
            continue
        if PROTOCOL_MARKER in text:
            spans.append(
                {
                    "page_from": number,
                    "page_to": number,
                    "start_marker": text.strip()[:80],
                    "title_hint": None,
                    "date_hint": (MONTH_LINE.search(text) or [None, None])[1],
                    "land_hint": _land(text),
                    "city_hint": (CITY_LINE.search(text) or [None, None])[1],
                    "confidence": 0.9,
                    "notes": None,
                }
            )
        else:
            unassigned.append(number)
    return {"spans": spans, "unassigned_pages": unassigned, "warnings": []}


def extraction(payload: dict[str, Any]) -> dict[str, Any]:
    """A small record from text markers, with one open question and one field uncertainty."""
    text = str(payload["text"])
    pages = [number for number, _ in _pages(text)] or [1]
    age = AGE.search(text)
    complaint = COMPLAINT.search(text)
    anamnesis = []
    if complaint:
        anamnesis.append(
            {
                "section": "aktuelle_beschwerden",
                "label_de": "Aktuelle Beschwerden",
                "value_de": complaint.group(1).strip(),
                "polarity": "present",
                "temporality": None,
                "quote_de": complaint.group(1).strip()[:200],
                "source_pages": pages[:1],
                "uncertainty": None,
            }
        )
    if "Allergien" not in text:
        anamnesis.append(
            {
                "section": "allergien",
                "label_de": "Allergien",
                "value_de": None,
                "polarity": "unknown",
                "temporality": None,
                "quote_de": None,
                "source_pages": [],
                "uncertainty": "Nicht im Protokoll erwähnt",
            }
        )
    passed = "bestanden" in text.lower()
    return {
        "location": {
            "land": _land(text),
            "city": (CITY_LINE.search(text) or [None, None])[1],
            "exam_body": None,
            "exam_date": (MONTH_LINE.search(text) or [None, None])[1],
            "specialty": None,
        },
        "patient": {
            "age_years": int(age.group(1)) if age else None,
            "sex": "unknown",
            "occupation_de": None,
            "presenting_complaint_de": complaint.group(1).strip() if complaint else None,
            "summary_de": None,
            "uncertainty": None if age else "Alter nicht angegeben",
        },
        "anamnesis": anamnesis,
        "diagnosis": {"suspected_de": None, "differentials_de": [], "uncertainty": None},
        "examiner_questions": [
            {
                "part": "arzt_arzt",
                "question_de": "Stellen Sie den Patienten vor.",
                "expected_answer_de": None,
                "candidate_answer_de": None,
                "source_pages": pages[:1],
                "uncertainty": None,
            }
        ],
        "arzt_arzt": {
            "presentation_summary_de": None,
            "discussion_points_de": [],
            "uncertainty": None,
        },
        "fachbegriffe": [
            {"german": "Dyspnoe", "lay_german": "Atemnot", "french": None, "asked": True}
        ],
        "arztbrief_notes_de": None,
        "outcome": {
            "result": "passed" if passed else "unknown",
            "examiner_feedback_de": [],
            "candidate_tips_de": [],
            "uncertainty": None,
        },
        "unresolved_questions": [
            {"text_fr": "Le motif de consultation est-il complet ?", "critical": True}
        ],
        "field_uncertainties": (
            []
            if passed
            else [{"path": "outcome.result", "reason_fr": "Résultat non indiqué dans le protocole"}]
        ),
        "pseudonymisation": {"removed_kinds": ["examiner_name"], "notes": []},
    }


def bundle_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """Fill every id of the skeleton with plain, predictable text. Never invents an id."""
    facts = payload["facts"]
    complaint = next((f for f in facts if f["section"] == "aktuelle_beschwerden"), None)
    opening_fact = complaint or (facts[0] if facts else None)
    persona = payload.get("persona_instruction_de", "kooperativ")
    complaint_de = payload["patient"].get("presenting_complaint_de") or "Anamnese"
    opens = bool(opening_fact and opening_fact["value_de"])
    return {
        "title_de": f"Fall {payload['case_id']}: {complaint_de}",
        "public_summary_fr": "Cas d'entraînement généré à partir d'un protocole gold, à relire.",
        "transcription_context_de": "Anamnesegespräch in der Notaufnahme, Fachsprachprüfung.",
        "unknown_response_de": "Das weiß ich nicht, das hat mir niemand gesagt.",
        "out_of_scope_response_de": "Entschuldigung, darüber möchte ich jetzt nicht sprechen.",
        "persona_de": f"Simulierte Patientin oder simulierter Patient, {persona}",
        "opening_de": (
            f"Guten Tag, Herr Doktor. {opening_fact['value_de']}"
            if opens and opening_fact
            else "Guten Tag, Herr Doktor."
        ),
        "opening_fact_ids": [opening_fact["id"]] if opens and opening_fact else [],
        "objectives_fr": ["Recueillir une anamnèse complète et structurée"],
        "facts": [
            {
                "fact_id": fact["id"],
                "patient_phrases_de": [
                    fact["value_de"] or f"{fact['label_de']}? Das weiß ich nicht."
                ],
                "translation_fr": None,
            }
            for fact in facts
        ],
        "empathy_moments": [],
        "behaviour_items": [
            {"item_id": item["item_id"], "doctor_phrases": PHRASES.get(item["item_id"], ["Danke"])}
            for item in payload["behaviour_items"]
        ],
        "practice_answers": [
            {
                "question_id": question["id"],
                "expected_behavior": f"Répondre : {question['prompt_de']}",
                "accepted_answers": [question["hint_de"] or f"Antwort auf {question['id']}"],
                "coaching_fr": "Réponds avec les seules informations du protocole.",
            }
            for question in payload["practice_questions"]
        ],
    }


PHRASES = {
    "greeting": ["Guten Tag", "Hallo"],
    "introduction": ["Mein Name ist", "Ich bin"],
    "closing": ["Zusammenfassend", "Ich fasse zusammen"],
}


FAKE_CONTENT_HANDLERS: dict[type[BaseModel], Callable[[dict[str, Any]], dict[str, Any]]] = {
    SegmentationOutput: segmentation,
    ExtractionOutput: extraction,
    BundleDraftOutput: bundle_draft,
}
