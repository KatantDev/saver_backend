from uuid import UUID

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from saver_backend.db.models.base_model import DbBaseModel
from saver_backend.entities.enums import SourceEnum


class HistoryModel(DbBaseModel):
    """Model for user content request history."""

    __tablename__ = "history"

    id: Mapped[UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    cache_id: Mapped[UUID | None] = mapped_column(
        PGUUID,
        ForeignKey("cache.id", ondelete="SET NULL"),
        nullable=True,
    )
    source: Mapped[SourceEnum] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
