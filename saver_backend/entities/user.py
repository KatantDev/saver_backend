from uuid import UUID

from pydantic import BaseModel

from saver_backend.db.models.user_model import UserModel


class UserDTO(BaseModel):
    """Information about a user."""

    id: UUID
    telegram_id: int
    username: str | None = None
    first_name: str
    last_name: str | None = None
    language: str
    is_admin: bool = False

    @classmethod
    def from_db(cls, model: UserModel) -> "UserDTO":
        """
        Create dto from the database model.

        :param model: database model.
        :return: internal DTO with info about user.
        """
        return cls(
            id=model.id,
            telegram_id=model.telegram_id,
            username=model.username,
            first_name=model.first_name,
            last_name=model.last_name,
            language=model.lang,
            is_admin=model.is_admin,
        )
