from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True, slots=True)
class VersionedPrompt:
    version: str
    content: str
    content_hash: str


def load_prompt(path: Path, version: str) -> VersionedPrompt:
    content = path.read_text(encoding="utf-8").strip()
    return VersionedPrompt(
        version=version,
        content=content,
        content_hash=sha256(content.encode()).hexdigest(),
    )
