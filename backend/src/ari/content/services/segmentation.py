"""Job `segment_document`: find where each protocol starts, deterministically stitched.

The model reads windows of pages; the code decides the windows, drops spans that belong to
the previous window, removes duplicates from the overlap, extends each protocol to the start
of the next one and flags spans too long to be one protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ari.content.domain.documents import (
    Document,
    DocumentSegment,
    DocumentStatus,
    Job,
    JobType,
    SegmentStatus,
)
from ari.content.ports import ContentRepository, JobQueue
from ari.content.schemas import SegmentationSpan
from ari.content.services.ai import ContentModel
from ari.domain.errors import NotFoundError
from ari.domain.models import new_id

WINDOW_PAGES = 25
OVERLAP_PAGES = 2
MAX_SEGMENT_PAGES = 12
PAGE_MARKER = "<<<SEITE {number}>>>"


def windows(page_count: int) -> list[tuple[int, int, int]]:
    """(first page, last page, first page this window is responsible for)."""
    result = []
    first = 1
    while first <= page_count:
        last = min(first + WINDOW_PAGES - 1, page_count)
        own_from = 1 if first == 1 else first + OVERLAP_PAGES
        result.append((first, last, min(own_from, last)))
        if last == page_count:
            break
        first = last + 1 - OVERLAP_PAGES
    return result


def render_pages(pages: dict[int, str], first: int, last: int) -> str:
    return "\n".join(
        f"{PAGE_MARKER.format(number=number)}\n{pages.get(number, '')}"
        for number in range(first, last + 1)
    )


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def marker_position(page_text: str, marker: str) -> int:
    """Where the marker starts in the page, whitespace-insensitive; large when not found."""
    tokens = normalise(marker).split(" ")
    pattern = r"\s+".join(re.escape(token) for token in tokens[:12] if token)
    match = re.search(pattern, page_text, flags=re.IGNORECASE) if pattern else None
    return match.start() if match else 10**9


@dataclass(frozen=True, slots=True)
class SpanDraft:
    span: SegmentationSpan
    window_last: int
    page_to: int


def merge_spans(
    collected: list[tuple[SegmentationSpan, int]], pages: dict[int, str], page_count: int
) -> list[SpanDraft]:
    """Deduplicate overlap hits and make protocols contiguous: each runs to the next start."""
    seen: set[tuple[int, str]] = set()
    unique: list[tuple[SegmentationSpan, int]] = []
    for span, window_last in collected:
        key = (span.page_from, normalise(span.start_marker)[:40])
        if key in seen or span.page_from < 1 or span.page_from > page_count:
            continue
        seen.add(key)
        unique.append((span, window_last))
    unique.sort(
        key=lambda item: (
            item[0].page_from,
            marker_position(pages.get(item[0].page_from, ""), item[0].start_marker),
        )
    )
    drafts: list[SpanDraft] = []
    for index, (span, window_last) in enumerate(unique):
        if index + 1 < len(unique):
            next_from = unique[index + 1][0].page_from
            page_to = max(span.page_from, max(span.page_to, next_from - 1))
            page_to = min(page_to, next_from)  # Two protocols may share a page.
        else:
            page_to = (
                page_count if span.page_to >= window_last else max(span.page_to, span.page_from)
            )
        drafts.append(SpanDraft(span=span, window_last=window_last, page_to=page_to))
    return drafts


class Segmentation:
    def __init__(self, repository: ContentRepository, model: ContentModel, queue: JobQueue) -> None:
        self._repository = repository
        self._model = model
        self._queue = queue

    async def run(self, job: Job) -> None:
        document_id = str(job.payload["document_id"])
        with self._repository.transaction() as tx:
            document = tx.get_document(document_id)
            if document is None:
                raise NotFoundError("Document inconnu")
            pages = {page.page_number: page.text for page in tx.pages(document_id)}
            existing = tx.segments(document_id)
        if existing:
            segments = existing  # Retry after a crash between storing segments and queueing.
        else:
            segments = await self._segment(job, document, pages)
        for segment in segments:
            if segment.status is SegmentStatus.PENDING:
                self._queue.enqueue(
                    JobType.EXTRACT_PROTOCOL,
                    {"document_id": document_id, "segment_id": segment.id},
                    document_id=document_id,
                )

    async def _segment(
        self, job: Job, document: Document, pages: dict[int, str]
    ) -> tuple[DocumentSegment, ...]:
        page_count = max(pages, default=0)
        collected: list[tuple[SegmentationSpan, int]] = []
        runs = []
        for first, last, own_from in windows(page_count):
            output, run = await self._model.segment(
                job_id=job.id,
                document_id=document.id,
                text=render_pages(pages, first, last),
                own_pages_from=own_from,
                declaration=document.declaration,
            )
            runs.append(run)
            collected.extend((span, last) for span in output.spans if span.page_from >= own_from)
        segments = tuple(
            DocumentSegment(
                id=new_id(),
                document_id=document.id,
                index=index,
                page_from=draft.span.page_from,
                page_to=draft.page_to,
                start_marker=draft.span.start_marker,
                confidence=draft.span.confidence,
                origin="ai",
                status=(
                    SegmentStatus.TOO_LONG
                    if draft.page_to - draft.span.page_from + 1 > MAX_SEGMENT_PAGES
                    else SegmentStatus.PENDING
                ),
                title_hint=draft.span.title_hint,
                date_hint=draft.span.date_hint,
                land_hint=draft.span.land_hint,
                city_hint=draft.span.city_hint,
            )
            for index, draft in enumerate(merge_spans(collected, pages, page_count))
        )
        with self._repository.transaction() as tx:
            for run in runs:
                tx.add_ai_run(run)
            tx.add_segments(segments)
            tx.set_document_status(document.id, DocumentStatus.SEGMENTED)
        return tuple(replace(segment) for segment in segments)
