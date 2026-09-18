from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from ari.infrastructure.persistence.platform.base import NAMING_CONVENTION


class ContentBase(DeclarativeBase):
    """Separate metadata so autogenerate never mixes the two databases' schemas."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
