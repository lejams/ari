"""Platform database: learners, sessions, the clinical registry and the placement registry.

Importing this package registers every row module on ``Base.metadata`` so Alembic and the
schema helpers see the complete platform schema.
"""

from ari.infrastructure.persistence.platform import account_rows as account_rows
from ari.infrastructure.persistence.platform.base import Base
from ari.infrastructure.persistence.platform.engine import create_platform_engine
from ari.infrastructure.persistence.platform.repository import SqlSessionRepository

__all__ = ["Base", "SqlSessionRepository", "create_platform_engine"]
