from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from saver_backend.db.models.base_model import DbBaseModel
from saver_backend.entities.enums import SourceEnum


class VideoCacheModel(DbBaseModel):
    """Model for caching video file_id from telegram."""

    __tablename__ = "video_cache"

    id: Mapped[UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    source: Mapped[SourceEnum] = mapped_column(
        String(50),
        nullable=False,
        doc="Source of the video (e.g., tiktok, youtube_shorts_ydl)",
    )
    source_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Unique ID of the video from the source platform (e.g., yt-dlp's 'id')",
    )
    file_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Telegram's unique file_id for the video",
    )
    file_unique_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Telegram's unique and persistent file_unique_id",
    )
    meta_data: Mapped[dict[str, Any] | list[Any]] = mapped_column(
        JSON,
        nullable=True,
        doc="Additional metadata, e.g., title, quality, duration",
    )

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_source_source_id"),
        Index("ix_video_cache_source_id", "source_id"),
    )
