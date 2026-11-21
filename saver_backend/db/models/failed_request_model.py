from uuid import UUID

from sqlalchemy import BigInteger, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from saver_backend.db.models.base_model import DbBaseModel
from saver_backend.entities.enums import SourceEnum


class FailedRequestModel(DbBaseModel):
    """
    A request the user got an error for, with the full link.

    Logs of the worker are lost on every deploy and Sentry drops the large
    yt-dlp events, so failed links were not kept anywhere. The owners asked to
    keep the full link to see which content fails and why.
    """

    __tablename__ = "failed_request"

    id: Mapped[UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[SourceEnum] = mapped_column(String(50), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
