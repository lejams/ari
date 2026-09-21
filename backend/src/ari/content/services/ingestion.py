"""Upload of a protocol PDF: checked, stored by content hash, declared, queued for extraction."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePath

from ari.content.domain.documents import (
    Actor,
    Document,
    DocumentDeclaration,
    JobType,
    UploadedVia,
)
from ari.content.ports import ContentRepository, DocumentStorage, JobQueue
from ari.domain.errors import InvalidStateError


@dataclass(frozen=True, slots=True)
class IngestResult:
    document: Document
    duplicate: bool


class DocumentIngestion:
    def __init__(
        self,
        repository: ContentRepository,
        storage: DocumentStorage,
        queue: JobQueue,
        *,
        max_bytes: int,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._queue = queue
        self._max_bytes = max_bytes

    def ingest(
        self,
        data: bytes,
        filename: str,
        declaration: DocumentDeclaration,
        *,
        actor: Actor,
        via: UploadedVia,
    ) -> IngestResult:
        if not data.startswith(b"%PDF"):
            raise InvalidStateError("Le fichier n'est pas un PDF")
        if len(data) > self._max_bytes:
            raise InvalidStateError("Fichier trop volumineux")
        digest = sha256(data).hexdigest()
        with self._repository.transaction() as tx:
            existing = tx.get_document(digest)
            if existing is not None:
                # Same bytes, same document: the earlier declaration and pipeline state win.
                return IngestResult(existing, duplicate=True)
            key = f"documents/{digest[:2]}/{digest}.pdf"
            self._storage.put(data, key=key)
            document = Document(
                id=digest,
                filename=PurePath(filename).name or "document.pdf",
                size_bytes=len(data),
                storage_key=key,
                uploaded_via=via,
                declaration=declaration,
                uploaded_by_account_id=actor.account_id,
            )
            tx.add_document(document)
        self._queue.enqueue(JobType.EXTRACT_TEXT, {"document_id": digest}, document_id=digest)
        return IngestResult(document, duplicate=False)
