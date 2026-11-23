from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from saver_backend.db.models.base_model import DbBaseModel


class ErrorEventModel(DbBaseModel):
    """
    An error logged by the bot: one row per kind of error per day.

    The bot's own error journal, kept because the Sentry server stores almost
    no errors and the worker log is lost on every deploy.
    """

    __tablename__ = "error_event"
    __table_args__ = (
        UniqueConstraint("fingerprint", "day", name="uq_error_event_fingerprint_day"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    fingerprint: Mapped[str] = mapped_column(String(40), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    process: Mapped[str] = mapped_column(String(32), nullable=False)
    logger: Mapped[str] = mapped_column(String(255), nullable=False)
    error_type: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
