"""Conservative contact detection; never a claim of complete anonymization."""

import re
from typing import Any

CONTACT = re.compile(
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"
    r"|(?:https?://)?(?:t\.me|telegram\.me)/[^\s]+"
    r"|(?<!\w)@[A-Za-z][A-Za-z0-9_]{3,}\b"
    r"|(?<!\w)\+\d[\d ()-]{7,}\d"
    r"|\b(?:Tel(?:efon)?|Phone|Mobil|WhatsApp)\s*[:=]?\s*\d[\d /()-]{6,}\d"
    r"|\b0\d{1,3}[ -]\d{2,4}[ -]\d{2,4}(?:[ -]\d{2,4})+\b",
    re.IGNORECASE,
)


def contact_locations(value: Any, path: str = "") -> list[str]:
    if isinstance(value, str):
        return [path] if CONTACT.search(value) else []
    if isinstance(value, dict):
        return [p for key, item in value.items() for p in contact_locations(item, f"{path}.{key}")]
    if isinstance(value, (list, tuple)):
        return [
            p
            for index, item in enumerate(value)
            for p in contact_locations(item, f"{path}[{index}]")
        ]
    return []


def redact_contacts(value: Any) -> Any:
    if isinstance(value, str):
        return CONTACT.sub("[contact retiré]", value)
    if isinstance(value, dict):
        return {key: redact_contacts(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_contacts(item) for item in value]
    return value
