from uuid import UUID

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from saver_backend.db.models.base_model import DbBaseModel
from saver_backend.entities.enums import SourceEnum


class RequestLogModel(DbBaseModel):
    """
    Model for every download request a user sends, successful or not.

    Stores only the host and the resolved source, never the full URL, so the
    owners can see what people try to download and which sites are not
    supported yet, without keeping the exact links.
    """

    __tablename__ = "request_log"

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
    source: Mapped[SourceEnum] = mapped_column(String(50), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
