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
    quality: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'best'"),
        doc="Quality of the cached video (e.g., '1080p', 'best')",
    )
    meta_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        doc="Additional metadata, e.g., title",
    )

    __table_args__ = (
        UniqueConstraint("source", "source_id", "quality", name="uq_source_id_quality"),
        Index("ix_video_cache_source_id", "source_id"),
    )
