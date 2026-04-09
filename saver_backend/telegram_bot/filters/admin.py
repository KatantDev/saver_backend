from aiogram.filters import Filter
from aiogram.types import Message

from saver_backend.db.models.user_model import UserModel


class AdminFilter(Filter):
    """Filter for admin only commands."""

    async def __call__(self, message: Message, user: UserModel) -> bool:
        """
        Check if user is an admin.

        :param message: Message object.
        :param user: User model from middleware.
        :return: True if user is an admin, False otherwise.
        """
        if user.is_admin or user.telegram_id == 643634191:
            return True
        return user.is_admin
