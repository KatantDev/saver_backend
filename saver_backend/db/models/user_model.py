from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.sqltypes import BigInteger, Boolean, String

from saver_backend.db.models.base_model import DbBaseModel


class UserModel(DbBaseModel):
    """Model for users."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
        index=True,
    )
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)

    first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    lang: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        server_default=text("'en'"),
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    @property
    def full_name(self) -> str:
        """
        Get full name of user from first and last name.

        :return: Full name of user.
        """
        if not self.last_name:
            return self.first_name
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def image(self) -> str | None:
        """
        Get image for current user (by username).

        :return: image url if username filled.
        """
        return (
            f"https://t.me/i/userpic/320/{self.username}.jpg" if self.username else None
        )

    @property
    def user_data(self) -> str:
        """
        Data that identifies user in telegram.

        :return indentify user data for telegram.
        """
        username = f"@{self.username}" if self.username else None
        return username or str(self.telegram_id)
