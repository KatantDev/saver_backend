from datetime import datetime

from sqlalchemy import DateTime, text
from sqlalchemy.orm import Mapped, mapped_column

from saver_backend.db.base import Base


class DbBaseModel(Base):
    """Base model for all models with common columns."""

    __abstract__ = True

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )
