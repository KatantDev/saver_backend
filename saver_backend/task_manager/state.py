from typing import TYPE_CHECKING, Annotated

from sqlalchemy.ext.asyncio import AsyncSession
from taskiq import TaskiqDepends, TaskiqState

from saver_backend.db.dao.cache_dao import CacheDAO
from saver_backend.db.dao.user_dao import UserDAO
from saver_backend.services.downloaders.resolver import SourceResolver
from saver_backend.task_manager.dependencies import get_session

if TYPE_CHECKING:
    from saver_backend.services.telegram.bot_controller import TelegramBotController


class DatabaseState:
    """Database state with DAOs and session."""

    def __init__(
        self,
        session: Annotated[AsyncSession, TaskiqDepends(get_session)],
    ) -> None:
        self.session = session

    @property
    def user_dao(self) -> "UserDAO":
        """
        Get user DAO.

        :return: User DAO.
        """
        return UserDAO(session=self.session)

    @property
    def cache_dao(self) -> "CacheDAO":
        """
        Get cache DAO.

        :return: CacheDAO instance.
        """
        return CacheDAO(session=self.session)


class SaverState:
    """Base state with additional services."""

    def __init__(
        self,
        state: Annotated[TaskiqState, TaskiqDepends()],
    ) -> None:
        self._state = state
        self.source_resolver = SourceResolver()

    @property
    def telegram_bot_controller(self) -> "TelegramBotController":
        """
        Get telegram bot controller.

        :return: Telegram bot controller.
        """
        return self._state.telegram_bot_controller
