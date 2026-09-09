"""Read-only XLSX reconciliation against the observed six-sheet Work projection.

No spreadsheet formulas, macros, external resources or source instructions execute.
"""

import json
import posixpath
import re
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from ari.infrastructure.cases.freiburg_format import FreiburgExport

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def workbook_projection(export: FreiburgExport) -> dict[str, list[dict[str, Any]]]:
    """Explicit flattening only; missing answers remain null, not inferred."""
    raw = export.model_dump(mode="json")
    cases: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    for case in raw["cases"]:
        row = {
            k: case[k]
            for k in (
                "case_id",
                "version",
                "status",
                "title_de",
                "title_fr",
                "language",
                "region",
                "city",
                "fsp_phase",
                "source_exam_date",
                "source_pages",
            )
        }
        row.update(
            {
                "reported_diagnosis_de": case["reported_diagnosis"]["de"],
                "reported_diagnosis_fr": case["reported_diagnosis"]["fr"],
                "medically_validated": case["reported_diagnosis"]["medically_validated"],
                "patient_name": case["demographics"]["name"],
            }
        )
        row.update(
            {k: v for k, v in case["demographics"].items() if k not in ("name", "needs_review")}
        )
        row.update(
            {
                "opening_line_de": case["opening_line_de"],
                "opening_line_fr": case["opening_line_fr"],
                "fact_count": len(case["facts"]),
                "assessment_item_count": len(case["assessment_items"]),
                "needs_review": case["needs_review"],
            }
        )
        cases.append(row)
        for fact in case["facts"]:
            facts.append(
                {
                    "case_id": case["case_id"],
                    **{
                        ("medication_struct" if k == "medication" else k): v
                        for k, v in fact.items()
                    },
                }
            )
        assessments.extend({"case_id": case["case_id"], **a} for a in case["assessment_items"])
    return {
        "Sources": raw["sources"],
        "Cases": cases,
        "Facts": facts,
        "Assessment Items": assessments,
        "Terminology": [
            {"term_id": f"TERM-{i:03d}", **t} for i, t in enumerate(raw["terminology"], 1)
        ],
        "Open Questions": raw["open_questions"],
    }


def _xml(archive: ZipFile, path: str) -> ET.Element:
    data = archive.read(path)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("Déclarations XML non autorisées")
    return ET.fromstring(data)


def read_workbook(data: bytes) -> dict[str, list[list[Any]]]:
    result: dict[str, list[list[Any]]] = {}
    with ZipFile(BytesIO(data)) as archive:
        if sum(f.file_size for f in archive.infolist()) > 20_000_000:
            raise ValueError("Classeur trop volumineux")
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = [
                "".join(node.itertext())
                for node in _xml(archive, "xl/sharedStrings.xml").findall("s:si", NS)
            ]
        rels = {r.attrib["Id"]: r.attrib for r in _xml(archive, "xl/_rels/workbook.xml.rels")}
        for sheet in _xml(archive, "xl/workbook.xml").findall("s:sheets/s:sheet", NS):
            name = sheet.attrib["name"]
            if name in result:
                raise ValueError("Onglet dupliqué")
            relation = rels[sheet.attrib[f"{{{REL}}}id"]]
            if relation.get("TargetMode") == "External":
                raise ValueError("Onglet externe interdit")
            target = relation["Target"]
            path = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else "xl/" + target
            )
            if not path.startswith("xl/worksheets/"):
                raise ValueError("Référence d'onglet non reconnue")
            cells: dict[tuple[int, int], Any] = {}
            for cell in _xml(archive, path).findall("s:sheetData/s:row/s:c", NS):
                if cell.find("s:f", NS) is not None:
                    raise ValueError("Formule interdite dans l'export de données")
                address = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,4})", cell.attrib["r"])
                if address is None:
                    raise ValueError("Adresse de cellule invalide")
                col = 0
                for letter in address[1]:
                    col = col * 26 + ord(letter) - ord("A") + 1
                row = int(address[2])
                if row > 5000 or col > 100 or (row, col) in cells:
                    raise ValueError("Cellule hors limites ou dupliquée")
                value: Any = cell.findtext("s:v", namespaces=NS)
                kind = cell.get("t")
                if kind == "inlineStr":
                    value = "".join(n.text or "" for n in cell.findall("s:is//s:t", NS)) or None
                elif value is not None:
                    if kind == "s":
                        value = strings[int(value)]
                    elif kind == "str":
                        pass
                    elif kind == "b" and value in ("0", "1"):
                        value = value == "1"
                    elif kind in (None, "n"):
                        value = json.loads(value)
                        if type(value) not in (int, float):
                            raise ValueError("Cellule numérique invalide")
                    else:
                        raise ValueError("Type de cellule non pris en charge")
                cells[row, col] = value
            rows = max((r for r, c in cells), default=0)
            cols = max((c for r, c in cells), default=0)
            result[name] = [
                [cells.get((r, c)) for c in range(1, cols + 1)] for r in range(1, rows + 1)
            ]
    return result


def verify_workbook(export: FreiburgExport, data: bytes) -> dict[str, int]:
    expected, actual = workbook_projection(export), read_workbook(data)
    if set(expected) != set(actual):
        raise ValueError("Les six onglets attendus doivent être présents, sans onglet ajouté")
    counts = {}
    for name, records in expected.items():
        rows = actual[name]
        if not rows or len(rows) != len(records) + 1:
            raise ValueError(f"Nombre de lignes incohérent : {name}")
        headers = rows[0]
        if len(set(headers)) != len(headers) or any(not isinstance(h, str) for h in headers):
            raise ValueError(f"En-têtes invalides : {name}")
        if records and set(headers) != set(records[0]):
            raise ValueError(f"Colonnes incompatibles : {name}")
        for index, (record, values) in enumerate(zip(records, rows[1:], strict=True), 2):
            for header, value in zip(headers, values, strict=True):
                wanted = record[header]
                if isinstance(wanted, list):
                    wanted = ", ".join(str(v) for v in wanted) or None
                elif isinstance(wanted, dict):
                    try:
                        value = json.loads(value)
                    except (ValueError, TypeError) as exc:
                        raise ValueError(f"Structure médicament invalide : {name}/{index}") from exc
                if wanted != value or isinstance(wanted, bool) != isinstance(value, bool):
                    raise ValueError(f"Écart JSON/XLSX : {name}, ligne {index}, colonne {header}")
        counts[name] = len(records)
    return counts
