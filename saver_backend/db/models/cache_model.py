from functools import cached_property
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from saver_backend.db.models.base_model import DbBaseModel
from saver_backend.entities.enums import ContentTypeEnum, SourceEnum
from saver_backend.entities.mappers import CONTENT_TYPE_TO_DTO_MAP
from saver_backend.services.downloaders.schema import (
    CacheableDTO,
)


class CacheModel(DbBaseModel):
    """Model for caching any content file_id from telegram."""

    __tablename__ = "cache"

    id: Mapped[UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    source: Mapped[SourceEnum] = mapped_column(
        String(50),
        nullable=False,
        doc="Source of the content (e.g., tiktok, youtube_shorts_ydl)",
    )
    source_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Unique ID of the content from the source platform",
    )
    file_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Telegram's unique file_id for the content",
    )
    file_unique_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Telegram's unique and persistent file_unique_id",
    )
    quality: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'best'"),
        doc="Quality of the cached content (e.g., '1080p', 'best')",
    )
    content_type: Mapped[ContentTypeEnum] = mapped_column(
        String(50),
        nullable=False,
        doc="Type of the cached content (e.g., video, photo)",
    )
    meta_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        doc="Additional metadata, e.g., title, corresponding DTO",
    )

    __table_args__ = (
        UniqueConstraint("source", "source_id", "quality", name="uq_source_id_quality"),
        Index("ix_cache_source_id", "source_id"),
    )

    @cached_property
    def meta_data_dto(self) -> CacheableDTO | None:
        """
        Parse the meta_data JSON into a corresponding Pydantic DTO.

        :return: A parsed DTO object or None if parsing fails.
        """
        if not self.meta_data:
            return None

        dto_class = CONTENT_TYPE_TO_DTO_MAP.get(self.content_type)

        if dto_class:
            return dto_class.model_validate(self.meta_data)
        return None
