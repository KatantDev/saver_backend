from uuid import UUID

from saver_backend.db.dao.base_dao import BaseDAO
from saver_backend.db.models.history_model import HistoryModel
from saver_backend.entities.enums import SourceEnum


class HistoryDAO(BaseDAO):
    """Class for accessing the history table."""

    async def create(
        self,
        user_id: UUID,
        source: SourceEnum,
        url: str,
        cache_id: UUID | None = None,
    ) -> None:
        """
        Create a new history entry.

        :param user_id: The ID of the user.
        :param source: The source of the content.
        :param url: The original URL requested by the user.
        :param cache_id: Optional ID of the associated cache entry.
        """
        model = HistoryModel(
            user_id=user_id,
            cache_id=cache_id,
            source=source,
            url=url,
        )
        self.session.add(model)
        await self.session.flush()
