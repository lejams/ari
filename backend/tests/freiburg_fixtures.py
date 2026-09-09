"""Synthetic fixtures matching the observed transport shape. No real patient data."""

import json
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from ari.infrastructure.cases.freiburg_format import FreiburgExport
from ari.infrastructure.cases.freiburg_workbook import NS, REL, workbook_projection


def synthetic_freiburg() -> FreiburgExport:
    source = {
        "source_id": "synthetic-pdf",
        "document": "SYNTHETIC NOT CLINICAL.pdf",
        "url": "https://example.invalid/synthetic",
        "pages": "1-2",
        "document_date": None,
        "license": "GPLv3 [Frei] (SYNTHETIC declaration, unverified)",
        "checksum": None,
        "checksum_status": "unknown",
        "role": "SYNTHETIC TEST",
        "privacy": None,
        "needs_review": True,
    }
    question = {
        "de": "SYNTHETIC Frage?",
        "fr": "Question de test ?",
        "pages": [1],
        "needs_review": True,
    }
    fact = {
        "fact_id": "SYN-F1",
        "category": "family",
        "concept": "SYNTHETIC Vater",
        "polarity": "unknown",
        "value_de": "kein Kontakt",
        "value_fr": "pas de contact",
        "value_type": "text",
        "numeric_value": None,
        "unit": None,
        "temporal_de": None,
        "temporal_fr": None,
        "medication": None,
        "patient_phrase_de": "kein Kontakt",
        "founder_translation_fr": "pas de contact",
        "disclosure_rule": "answer_if_asked",
        "source_pages": [1],
        "needs_review": True,
    }
    info = {
        "assessment_item_id": "SYN-A1",
        "type": "information_gathering",
        "prompt_de": "SYNTHETIC Erheben",
        "prompt_fr": "Recueillir TEST",
        "weight": 1,
        "required": False,
        "satisfied_by_fact_ids": ["SYN-F1"],
        "source_pages": [1],
        "needs_review": True,
    }
    doctor = {
        **info,
        "assessment_item_id": "SYN-A2",
        "type": "arzt_arzt_question",
        "prompt_de": question["de"],
        "prompt_fr": question["fr"],
        "satisfied_by_fact_ids": [],
        "expected_answer_de": None,
        "expected_answer_fr": None,
    }
    term = {
        "term_de": "SYNTHETIC",
        "translation_fr": None,
        "source_pages": [1],
        "case_ids": ["SYN-CASE"],
        "note": None,
        "needs_review": True,
    }
    case = {
        "case_id": "SYN-CASE",
        "version": "0.1-draft",
        "status": "draft_unvalidated",
        "title_de": "SYNTHETIC TEST",
        "title_fr": "TEST SYNTHETIQUE",
        "language": "de-DE",
        "region": "TEST",
        "city": "TEST",
        "fsp_phase": "arzt_patient",
        "source_document_id": "synthetic-pdf",
        "source_pages": [1],
        "source_exam_date": "2026-01-01",
        "reported_diagnosis": {
            "de": "TEST",
            "fr": "TEST",
            "medically_validated": False,
            "qualifier": "reported_in_community_source",
        },
        "demographics": {
            "name": None,
            "age_years": None,
            "sex": "unknown",
            "height_cm": None,
            "weight_kg": None,
            "occupation_de": None,
            "occupation_fr": None,
            "marital_status_de": None,
            "marital_status_fr": None,
            "children": None,
            "needs_review": True,
        },
        "opening_line_de": None,
        "opening_line_fr": None,
        "facts": [fact],
        "assessment_items": [info, doctor],
        "terminology": [term],
        "arzt_arzt_questions": [question],
        "open_questions": ["SYNTHETIC contradiction"],
        "publication_note_fr": "TEST NON VALIDE",
        "needs_review": True,
    }
    return FreiburgExport.model_validate_json(
        json.dumps(
            {
                "schema_version": "ari-clinical-cases-draft-0.1",
                "generated_on": "2026-01-01",
                "status": "draft_unvalidated",
                "validation": {
                    "medical": False,
                    "linguistic": False,
                    "human_review_required": True,
                },
                "sources": [source],
                "cases": [case],
                "terminology": [term],
                "open_questions": [
                    {
                        "question_id": "SYN-Q1",
                        "case_id": "SYN-CASE",
                        "category": "test",
                        "question_fr": "SYNTHETIC contradiction",
                        "source_pages": [1],
                        "needs_review": True,
                    }
                ],
                "privacy_note": "SYNTHETIC DATA ONLY",
            }
        )
    )


def synthetic_workbook(export: FreiburgExport, *, formula: bool = False) -> bytes:
    """Test-only XLSX sheet XML, independent of spreadsheet authoring dependencies."""
    stream = BytesIO()
    workbook = ET.Element("workbook", xmlns=NS["s"])
    sheets = ET.SubElement(workbook, "sheets")
    rels = ET.Element("Relationships")
    with ZipFile(stream, "w") as archive:
        for index, (name, records) in enumerate(workbook_projection(export).items(), 1):
            ET.SubElement(sheets, "sheet", {"name": name, f"{{{REL}}}id": f"rId{index}"})
            ET.SubElement(
                rels,
                "Relationship",
                {"Id": f"rId{index}", "Target": f"worksheets/sheet{index}.xml"},
            )
            sheet = ET.Element("worksheet", xmlns=NS["s"])
            data = ET.SubElement(sheet, "sheetData")
            headers = list(records[0])
            rows = [headers, *[[r[h] for h in headers] for r in records]]
            for row_index, values in enumerate(rows, 1):
                row = ET.SubElement(data, "row", r=str(row_index))
                for col, value in enumerate(values, 1):
                    col_name, n = "", col
                    while n:
                        n, digit = divmod(n - 1, 26)
                        col_name = chr(65 + digit) + col_name
                    attrs = {"r": f"{col_name}{row_index}"}
                    if isinstance(value, list):
                        value = ", ".join(str(v) for v in value) or None
                    if isinstance(value, dict):
                        value = json.dumps(value)
                    text: Any = value
                    if isinstance(value, str):
                        attrs["t"] = "str"
                    elif isinstance(value, bool):
                        attrs["t"], text = "b", str(int(value))
                    elif value is not None:
                        attrs["t"], text = "n", str(value)
                    cell = ET.SubElement(row, "c", attrs)
                    if value is not None:
                        ET.SubElement(cell, "v").text = text
                    if formula and name == "Facts" and row_index == 2 and col == 1:
                        ET.SubElement(cell, "f").text = "1+1"
            archive.writestr(f"xl/worksheets/sheet{index}.xml", ET.tostring(sheet))
        archive.writestr("xl/workbook.xml", ET.tostring(workbook))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(rels))
    return stream.getvalue()
