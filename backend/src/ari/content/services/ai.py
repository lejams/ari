"""The pipeline's model calls: versioned prompts, strict schemas, one AiRun per call."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from ari.application.contracts import ExecutionContext, LLMRequest
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.content.domain.documents import AiRun, DocumentDeclaration
from ari.content.domain.protocol import PROTOCOL_SCHEMA_VERSION
from ari.content.schemas import ExtractionOutput, SegmentationOutput
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord

T = TypeVar("T", bound=BaseModel)

SEGMENTATION_OPERATION = "protocol_segmentation"
EXTRACTION_OPERATION = "protocol_extraction"


@dataclass(frozen=True, slots=True)
class ContentPrompts:
    segmentation: VersionedPrompt
    extraction: VersionedPrompt


class ModelCallFailed(Exception):
    """The provider failed; the run is recorded by the caller before the job is retried."""

    def __init__(self, run: AiRun) -> None:
        super().__init__(run.error_message or run.error_code or "model call failed")
        self.run = run


def declared_payload(declaration: DocumentDeclaration) -> dict[str, Any]:
    return {
        "land": declaration.land.value if declaration.land else None,
        "city": declaration.city,
        "exam_body": declaration.exam_body,
        "exam_date": declaration.exam_date,
        "specialty": declaration.specialty,
    }


class ContentModel:
    def __init__(self, llm: LLMProvider, prompts: ContentPrompts) -> None:
        self._llm = llm
        self._prompts = prompts

    async def segment(
        self,
        *,
        job_id: str,
        document_id: str,
        text: str,
        own_pages_from: int,
        declaration: DocumentDeclaration,
    ) -> tuple[SegmentationOutput, AiRun]:
        payload = {
            "text": text,
            "own_pages_from": own_pages_from,
            "declared": declared_payload(declaration),
        }
        return await self._call(
            self._prompts.segmentation,
            SEGMENTATION_OPERATION,
            SegmentationOutput,
            payload,
            job_id=job_id,
            document_id=document_id,
        )

    async def extract(
        self,
        *,
        job_id: str,
        document_id: str,
        segment_id: str,
        text: str,
        declaration: DocumentDeclaration,
    ) -> tuple[ExtractionOutput, AiRun]:
        payload = {"text": text, "declared": declared_payload(declaration)}
        return await self._call(
            self._prompts.extraction,
            EXTRACTION_OPERATION,
            ExtractionOutput,
            payload,
            job_id=job_id,
            document_id=document_id,
            segment_id=segment_id,
        )

    async def _call(
        self,
        prompt: VersionedPrompt,
        operation: str,
        schema: type[T],
        payload: dict[str, Any],
        *,
        job_id: str,
        document_id: str,
        segment_id: str | None = None,
    ) -> tuple[T, AiRun]:
        body = json.dumps(payload, ensure_ascii=False)
        input_hash = sha256(body.encode("utf-8")).hexdigest()
        context = ExecutionContext(
            session_id=job_id,
            operation=operation,
            case_version=PROTOCOL_SCHEMA_VERSION,
            case_hash=input_hash,
            prompt_version=prompt.version,
            prompt_hash=prompt.content_hash,
        )
        request = LLMRequest(
            messages=(
                {"role": "system", "content": prompt.content},
                {"role": "user", "content": body},
            ),
            context=context,
        )

        def run_from(execution: ExecutionRecord) -> AiRun:
            return AiRun(
                id=execution.id,
                job_id=job_id,
                operation=operation,
                provider=execution.provider,
                model=execution.model,
                prompt_version=execution.prompt_version,
                prompt_hash=execution.prompt_hash,
                schema_name=schema.__name__,
                input_hash=input_hash,
                document_id=document_id,
                segment_id=segment_id,
                status=execution.status.value,
                latency_ms=execution.latency_ms,
                usage=dict(execution.usage),
                provider_request_id=execution.provider_request_id,
                error_code=execution.error_code,
                error_message=execution.error_message,
                retryable=execution.retryable,
                created_at=execution.created_at,
            )

        try:
            result = await self._llm.generate_structured(request, schema)
        except ProviderError as exc:
            raise ModelCallFailed(run_from(cast(ExecutionRecord, exc.execution))) from exc
        return result.value, run_from(result.execution)
