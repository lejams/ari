"""Composition root of the content pipeline: the only module here that touches infrastructure."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ari.application.ports.llm import LLMProvider
from ari.application.prompting import load_prompt
from ari.config import Settings
from ari.content.domain.documents import Job, JobType
from ari.content.fake_handlers import FAKE_CONTENT_HANDLERS
from ari.content.ports import ContentRepository, DocumentStorage, JobQueue, PdfTextExtractor
from ari.content.services.ai import ContentModel, ContentPrompts
from ari.content.services.bundle_drafting import BundleDrafting
from ari.content.services.extraction import ProtocolExtraction
from ari.content.services.ingestion import DocumentIngestion
from ari.content.services.segmentation import Segmentation
from ari.content.services.text_extraction import TextExtraction
from ari.content.services.workflow import ProtocolWorkflow
from ari.infrastructure.pdf.pymupdf_extractor import PyMuPdfExtractor
from ari.infrastructure.persistence.content.engine import create_content_engine
from ari.infrastructure.persistence.content.queue import PostgresJobQueue
from ari.infrastructure.persistence.content.repository import SqlContentRepository
from ari.infrastructure.providers.fake import FakeLLMProvider
from ari.infrastructure.storage.filesystem import FilesystemDocumentStorage

JobHandler = Callable[[Job], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ContentContainer:
    settings: Settings
    repository: ContentRepository
    queue: JobQueue
    storage: DocumentStorage
    extractor: PdfTextExtractor
    model: ContentModel
    ingestion: DocumentIngestion
    workflow: ProtocolWorkflow
    drafting: BundleDrafting
    handlers: dict[JobType, JobHandler]


def _llm(settings: Settings) -> LLMProvider:
    if settings.provider_mode == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("ARI_OPENAI_API_KEY est requis en mode openai")
        from ari.infrastructure.providers.openai.llm import OpenAILLMProvider

        return OpenAILLMProvider(
            settings.openai_api_key,
            patient_model=settings.patient_model,
            evaluation_model=settings.evaluation_model,
            patient_timeout_seconds=settings.patient_timeout_seconds,
            evaluation_timeout_seconds=settings.evaluation_timeout_seconds,
            content_model=settings.content_model,
            content_timeout_seconds=settings.content_timeout_seconds,
        )
    return FakeLLMProvider(handlers=FAKE_CONTENT_HANDLERS)


def build_content_container(
    settings: Settings, *, llm: LLMProvider | None = None
) -> ContentContainer:
    engine = create_content_engine(settings.content_database_url)
    repository = SqlContentRepository(engine)
    queue = PostgresJobQueue(engine)
    storage = FilesystemDocumentStorage(settings.content_storage_dir)
    extractor = PyMuPdfExtractor()
    prompts_dir = settings.prompt_directory / "content"
    model = ContentModel(
        llm or _llm(settings),
        ContentPrompts(
            segmentation=load_prompt(prompts_dir / "segmentation_v1.txt", "segmentation-v1"),
            extraction=load_prompt(
                prompts_dir / "protocol_extraction_v2.txt", "protocol-extraction-v2"
            ),
            bundle_draft=load_prompt(prompts_dir / "bundle_draft_v1.txt", "bundle-draft-v1"),
        ),
    )
    text_extraction = TextExtraction(repository, storage, extractor, queue)
    segmentation = Segmentation(repository, model, queue)
    extraction = ProtocolExtraction(repository, model)
    drafting = BundleDrafting(repository, model, queue)

    async def extract_text(job: Job) -> None:
        text_extraction.run(job)

    return ContentContainer(
        settings=settings,
        repository=repository,
        queue=queue,
        storage=storage,
        extractor=extractor,
        model=model,
        ingestion=DocumentIngestion(
            repository, storage, queue, max_bytes=settings.content_upload_max_bytes
        ),
        workflow=ProtocolWorkflow(repository),
        drafting=drafting,
        handlers={
            JobType.EXTRACT_TEXT: extract_text,
            JobType.SEGMENT_DOCUMENT: segmentation.run,
            JobType.EXTRACT_PROTOCOL: extraction.run,
            JobType.GENERATE_BUNDLE_DRAFT: drafting.run,
        },
    )
