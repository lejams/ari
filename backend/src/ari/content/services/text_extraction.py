"""Job `extract_text`: one row of text per page, then the document is ready to segment."""

from __future__ import annotations

from ari.content.domain.documents import DocumentPage, DocumentStatus, Job, JobType
from ari.content.ports import ContentRepository, DocumentStorage, JobQueue, PdfTextExtractor
from ari.domain.errors import NotFoundError


class TextExtraction:
    def __init__(
        self,
        repository: ContentRepository,
        storage: DocumentStorage,
        extractor: PdfTextExtractor,
        queue: JobQueue,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._extractor = extractor
        self._queue = queue

    def run(self, job: Job) -> None:
        document_id = str(job.payload["document_id"])
        with self._repository.transaction() as tx:
            document = tx.get_document(document_id)
            if document is None:
                raise NotFoundError("Document inconnu")
            existing = tx.pages(document_id)
            if existing:
                page_count = len(existing)  # A retry after a crash: the pages are already there.
            else:
                text = self._extractor.extract(self._storage.path(document.storage_key))
                tx.add_pages(
                    [
                        DocumentPage(
                            document_id=document_id,
                            page_number=number,
                            text=content,
                            extractor=text.extractor,
                        )
                        for number, content in enumerate(text.pages, start=1)
                    ]
                )
                page_count = text.page_count
            tx.set_document_status(
                document_id, DocumentStatus.TEXT_EXTRACTED, page_count=page_count
            )
        self._queue.enqueue(
            JobType.SEGMENT_DOCUMENT, {"document_id": document_id}, document_id=document_id
        )
