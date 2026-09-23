"""Job `extract_protocol`: one segment's text becomes the first version of a protocol record."""

from __future__ import annotations

from dataclasses import replace

from ari.content.domain.documents import (
    SYSTEM_ACTOR,
    Document,
    DocumentDeclaration,
    DocumentSegment,
    DocumentStatus,
    Job,
    ProtocolEvent,
    ProtocolVersion,
    SegmentStatus,
)
from ari.content.domain.pii import PII_DETECTOR_VERSION, scan
from ari.content.domain.protocol import (
    AnamnesisItem,
    ArztArztSummary,
    Diagnosis,
    ExaminerQuestion,
    Fachbegriff,
    FieldUncertainty,
    Outcome,
    PatientPresentation,
    Pedagogy,
    ProtocolLocation,
    ProtocolQuestion,
    ProtocolRecord,
    ProtocolSource,
    ProtocolStatus,
    PseudonymisationReport,
)
from ari.content.ports import ContentRepository
from ari.content.schemas import ExtractionOutput
from ari.content.services.ai import ContentModel
from ari.content.services.segmentation import PAGE_MARKER, marker_position
from ari.domain.errors import NotFoundError
from ari.domain.models import new_id

TERMINAL_SEGMENT_STATUSES = frozenset(
    {SegmentStatus.EXTRACTED, SegmentStatus.TOO_LONG, SegmentStatus.DISCARDED, SegmentStatus.FAILED}
)


def protocol_identifier(document_id: str, segment_index: int) -> str:
    return f"P-{document_id[:8]}-{segment_index:03d}"


def slice_text(
    pages: dict[int, str], segment: DocumentSegment, following: DocumentSegment | None
) -> str:
    """The segment's pages, cut at its own start marker and before the next protocol's."""
    parts = []
    for number in range(segment.page_from, segment.page_to + 1):
        text = pages.get(number, "")
        if number == segment.page_from:
            start = marker_position(text, segment.start_marker)
            text = text[start:] if start < len(text) else text
        if following is not None and number == following.page_from == segment.page_to:
            end = marker_position(text, following.start_marker)
            text = text[:end] if end < len(text) else text
        parts.append(f"{PAGE_MARKER.format(number=number)}\n{text}")
    return "\n".join(parts)


def _ids(prefix: str, count: int) -> list[str]:
    return [f"{prefix}{index:02d}" for index in range(1, count + 1)]


def _anamnesis_value_and_uncertainty(
    value_de: str | None, polarity: str, uncertainty: str | None
) -> tuple[str | None, str | None]:
    """Repair a tolerated model-contract violation before constructing the domain record."""
    if polarity != "unknown" or value_de is None:
        return value_de, uncertainty
    preserved = f"Valeur extraite avec polarité inconnue : {value_de}"
    return None, f"{uncertainty} — {preserved}" if uncertainty else preserved


def draft_record(
    output: ExtractionOutput, *, document: Document, segment: DocumentSegment
) -> ProtocolRecord:
    """Turn the model's output into a validated record: ids assigned, declaration applied.

    The uploader's declaration wins for Land and city (a city is never taken from the text);
    the model's Land is kept only when nothing was declared, with an uncertainty for the
    reviewer. Exam body, month and specialty fall back to the model's reading.
    """
    declared: DocumentDeclaration = document.declaration
    uncertainties = [
        FieldUncertainty(path=u.path, reason_fr=u.reason_fr) for u in output.field_uncertainties
    ]
    land = declared.land or output.location.land
    if land is None and all(u.path != "location.land" for u in uncertainties):
        uncertainties.append(
            FieldUncertainty(
                path="location.land",
                reason_fr="Land ni déclaré à l'import ni indiqué dans le protocole",
            )
        )
    elif (
        declared.land is None
        and output.location.land is not None
        and all(u.path != "location.land" for u in uncertainties)
    ):
        uncertainties.append(
            FieldUncertainty(
                path="location.land",
                reason_fr="Land lu dans le protocole, non déclaré à l'import : à confirmer",
            )
        )
    location = ProtocolLocation(
        land=land,
        city=declared.city,
        exam_body=declared.exam_body or output.location.exam_body,
        exam_date=declared.exam_date or output.location.exam_date,
        specialty=declared.specialty or output.location.specialty,
    )
    anamnesis = tuple(
        AnamnesisItem(
            id=identifier,
            section=item.section,
            label_de=item.label_de,
            value_de=value_de,
            polarity=item.polarity,
            temporality=item.temporality,
            quote_de=item.quote_de,
            source_pages=tuple(page for page in item.source_pages if page >= 1),
            uncertainty=uncertainty,
        )
        for identifier, item in zip(_ids("a", len(output.anamnesis)), output.anamnesis, strict=True)
        for value_de, uncertainty in [
            _anamnesis_value_and_uncertainty(item.value_de, item.polarity, item.uncertainty)
        ]
    )
    questions = tuple(
        ExaminerQuestion(
            id=identifier,
            part=question.part,
            question_de=question.question_de,
            expected_answer_de=question.expected_answer_de,
            candidate_answer_de=question.candidate_answer_de,
            source_pages=tuple(page for page in question.source_pages if page >= 1),
            uncertainty=question.uncertainty,
        )
        for identifier, question in zip(
            _ids("q", len(output.examiner_questions)), output.examiner_questions, strict=True
        )
    )
    terms = tuple(
        Fachbegriff(
            id=identifier,
            german=term.german,
            lay_german=term.lay_german,
            french=term.french,
            asked=term.asked,
        )
        for identifier, term in zip(
            _ids("t", len(output.fachbegriffe)), output.fachbegriffe, strict=True
        )
    )
    open_questions = tuple(
        ProtocolQuestion(id=identifier, text_fr=question.text_fr, critical=question.critical)
        for identifier, question in zip(
            _ids("u", len(output.unresolved_questions)), output.unresolved_questions, strict=True
        )
    )
    return ProtocolRecord(
        source=ProtocolSource(
            document_id=document.id,
            segment_index=segment.index,
            page_from=segment.page_from,
            page_to=segment.page_to,
            title_hint=segment.title_hint,
            date_hint=segment.date_hint,
            land_hint=segment.land_hint,
            city_hint=output.location.city or segment.city_hint,
        ),
        location=location,
        patient=PatientPresentation(
            age_years=output.patient.age_years
            if output.patient.age_years is None or 0 <= output.patient.age_years <= 120
            else None,
            sex=output.patient.sex,
            occupation_de=output.patient.occupation_de,
            presenting_complaint_de=output.patient.presenting_complaint_de,
            summary_de=output.patient.summary_de,
            uncertainty=output.patient.uncertainty,
        ),
        anamnesis=anamnesis,
        diagnosis=Diagnosis(
            suspected_de=output.diagnosis.suspected_de,
            differentials_de=tuple(output.diagnosis.differentials_de),
            uncertainty=output.diagnosis.uncertainty,
        ),
        examiner_questions=questions,
        arzt_arzt=ArztArztSummary(
            presentation_summary_de=output.arzt_arzt.presentation_summary_de,
            discussion_points_de=tuple(output.arzt_arzt.discussion_points_de),
            uncertainty=output.arzt_arzt.uncertainty,
        ),
        fachbegriffe=terms,
        arztbrief_notes_de=output.arztbrief_notes_de,
        outcome=Outcome(
            result=output.outcome.result,
            examiner_feedback_de=tuple(output.outcome.examiner_feedback_de),
            candidate_tips_de=tuple(output.outcome.candidate_tips_de),
            uncertainty=output.outcome.uncertainty,
        ),
        unresolved_questions=open_questions,
        field_uncertainties=tuple(uncertainties),
        pseudonymisation=PseudonymisationReport(
            removed_kinds=tuple(dict.fromkeys(output.pseudonymisation.removed_kinds)),
            notes=tuple(output.pseudonymisation.notes),
        ),
        pedagogy=Pedagogy(),
    )


class ProtocolExtraction:
    def __init__(self, repository: ContentRepository, model: ContentModel) -> None:
        self._repository = repository
        self._model = model

    async def run(self, job: Job) -> None:
        document_id = str(job.payload["document_id"])
        segment_id = str(job.payload["segment_id"])
        with self._repository.transaction() as tx:
            document = tx.get_document(document_id)
            segment = tx.get_segment(segment_id)
            if document is None or segment is None:
                raise NotFoundError("Document ou segment inconnu")
            if any(
                head.segment_id == segment_id for head in tx.list_heads(document_id=document_id)
            ):
                return  # Retry after a crash: the protocol already exists.
            pages = {page.page_number: page.text for page in tx.pages(document_id)}
            segments = tx.segments(document_id)
        following = next((s for s in segments if s.index == segment.index + 1), None)
        output, run = await self._model.extract(
            job_id=job.id,
            document_id=document_id,
            segment_id=segment_id,
            text=slice_text(pages, segment, following),
            declaration=document.declaration,
        )
        record = draft_record(output, document=document, segment=segment)
        protocol_id = protocol_identifier(document_id, segment.index)
        protocol = ProtocolVersion(
            id=protocol_id,
            version=1,
            record=record,
            document_id=document_id,
            segment_id=segment_id,
            created_via="ai",
            status=ProtocolStatus.EXTRACTED,
            pii_findings=scan(record.model_dump(mode="json")),
            pii_detector_version=PII_DETECTOR_VERSION,
        )
        with self._repository.transaction() as tx:
            tx.add_ai_run(replace(run, protocol_id=protocol_id))
            tx.add_protocol(protocol)
            tx.add_event(
                ProtocolEvent(
                    id=new_id(),
                    protocol_id=protocol_id,
                    protocol_version=1,
                    event_type="extracted",
                    actor=SYSTEM_ACTOR,
                    payload={"ai_run_id": run.id, "pii_findings": len(protocol.pii_findings)},
                )
            )
            tx.set_segment_status(segment_id, SegmentStatus.EXTRACTED)
            remaining = [
                s for s in tx.segments(document_id) if s.status not in TERMINAL_SEGMENT_STATUSES
            ]
            if not remaining:
                tx.set_document_status(document_id, DocumentStatus.EXTRACTED)
