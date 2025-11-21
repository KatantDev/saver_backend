from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from saver_backend.db.dao.base_dao import BaseDAO
from saver_backend.db.models.cache_model import CacheModel
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

    async def get_user_history_with_cache(
        self,
        user_id: UUID,
        sources: list[SourceEnum],
        limit: int = 20,
    ) -> list[CacheModel]:
        """
        Get the latest unique cached items from a user's history.

        :param user_id: The ID of the user.
        :param limit: The maximum number of items to return.
        :param sources: Optional list of sources to filter by.
        :return: A list of CacheModel instances.
        """
        subq = (
            select(HistoryModel.cache_id, HistoryModel.created_at)
            .join(HistoryModel.cache)
            .where(
                HistoryModel.user_id == user_id,
                HistoryModel.cache_id.isnot(None),
                HistoryModel.source.in_(sources),
            )
            .distinct(CacheModel.file_unique_id)
            .order_by(CacheModel.file_unique_id, HistoryModel.created_at.desc())
            .subquery()
        )

        query = (
            select(CacheModel)
            .join(subq, CacheModel.id == subq.c.cache_id)
            .order_by(subq.c.created_at.desc())
            .limit(limit)
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_count(self, created_after: datetime | None = None) -> int:
        """
        Get count of history records.

        :param created_after: datetime to filter records.
        :return: count of records.
        """
        query = select(func.count()).select_from(HistoryModel)
        if created_after:
            query = query.where(HistoryModel.created_at >= created_after)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def get_active_users_count(self, created_after: datetime) -> int:
        """
        Get count of active users.

        Active user is a user who has at least one record in history.

        :param created_after: datetime to filter records.
        :return: count of active users.
        """
        query = select(func.count(func.distinct(HistoryModel.user_id))).where(
            HistoryModel.created_at >= created_after,
        )
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def get_counts_by_source(self) -> list[tuple[SourceEnum, int]]:
        """
        Get counts of history records by source.

        :return: list of tuples (source, count).
        """
        query = (
            select(HistoryModel.source, func.count(HistoryModel.id))
            .group_by(HistoryModel.source)
            .order_by(func.count(HistoryModel.id).desc())
        )
        result = await self.session.execute(query)
        rows = result.all()
        return [(SourceEnum(source), count) for source, count in rows]
