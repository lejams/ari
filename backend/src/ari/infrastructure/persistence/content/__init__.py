"""Content database: uploaded protocol PDFs, extraction jobs, protocol versions, reviews, gold.

Separate metadata and Alembic chain from the platform database. The learner application
never holds credentials for it; the back-office and the worker do.
"""

from ari.infrastructure.persistence.content.auth_rows import (
    BackofficeAccountRow,
    BackofficeAuditRow,
    BackofficeInvitationRow,
    BackofficeSessionRow,
)
from ari.infrastructure.persistence.content.base import ContentBase
from ari.infrastructure.persistence.content.engine import create_content_engine
from ari.infrastructure.persistence.content.rows import (
    AiRunRow,
    BundleDraftRow,
    DocumentPageRow,
    DocumentRow,
    DocumentSegmentRow,
    GoldProtocolRow,
    JobRow,
    ProtocolEventRow,
    ProtocolReviewRow,
    ProtocolRow,
)

__all__ = [
    "AiRunRow",
    "BackofficeAccountRow",
    "BackofficeAuditRow",
    "BackofficeInvitationRow",
    "BackofficeSessionRow",
    "BundleDraftRow",
    "ContentBase",
    "DocumentPageRow",
    "DocumentRow",
    "DocumentSegmentRow",
    "GoldProtocolRow",
    "JobRow",
    "ProtocolEventRow",
    "ProtocolReviewRow",
    "ProtocolRow",
    "create_content_engine",
]
