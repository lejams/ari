"""Deterministic stand-ins for the content prompts, registered on the fake LLM provider.

They read the same JSON payload the real prompts receive and derive their answer from simple
text markers, so the pipeline runs end to end offline with predictable results.
"""

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from ari.content.schemas import ExtractionOutput, SegmentationOutput
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


FAKE_CONTENT_HANDLERS: dict[type[BaseModel], Callable[[dict[str, Any]], dict[str, Any]]] = {
    SegmentationOutput: segmentation,
    ExtractionOutput: extraction,
}
