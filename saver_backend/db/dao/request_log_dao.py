from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from saver_backend.db.dao.base_dao import BaseDAO
from saver_backend.db.models.request_log_model import RequestLogModel
from saver_backend.entities.enums import SourceEnum


class RequestLogDAO(BaseDAO):
    """Class for accessing the request_log table."""

    async def create(
        self,
        user_id: UUID,
        source: SourceEnum,
        host: str,
    ) -> None:
        """
        Record one download request.

        :param user_id: The ID of the user who sent the request.
        :param source: The resolved source (UNSUPPORTED if no detector matched).
        :param host: The host of the requested URL (no full URL is stored).
        """
        model = RequestLogModel(user_id=user_id, source=source, host=host)
        self.session.add(model)
        await self._release()

    async def get_count(self, created_after: datetime | None = None) -> int:
        """
        Count requests, optionally within a period.

        :param created_after: Only count requests newer than this.
        :return: The number of requests.
        """
        query = select(func.count()).select_from(RequestLogModel)
        if created_after:
            query = query.where(RequestLogModel.created_at >= created_after)
        return (await self.session.execute(query)).scalar_one()

    async def get_counts_by_source(
        self,
        created_after: datetime | None = None,
    ) -> list[tuple[SourceEnum, int]]:
        """
        Count requests grouped by source, most requested first.

        :param created_after: Only count requests newer than this.
        :return: List of (source, count).
        """
        query = (
            select(RequestLogModel.source, func.count(RequestLogModel.id))
            .group_by(RequestLogModel.source)
            .order_by(func.count(RequestLogModel.id).desc())
        )
        if created_after:
            query = query.where(RequestLogModel.created_at >= created_after)
        rows = (await self.session.execute(query)).all()
        return [(SourceEnum(source), count) for source, count in rows]

    async def get_top_unsupported_hosts(
        self,
        created_after: datetime | None = None,
        limit: int = 15,
    ) -> list[tuple[str, int]]:
        """
        Most requested hosts that no detector supports, most requested first.

        :param created_after: Only count requests newer than this.
        :param limit: Maximum number of hosts to return.
        :return: List of (host, count).
        """
        query = (
            select(RequestLogModel.host, func.count(RequestLogModel.id))
            .where(RequestLogModel.source == SourceEnum.UNSUPPORTED)
            .group_by(RequestLogModel.host)
            .order_by(func.count(RequestLogModel.id).desc())
            .limit(limit)
        )
        if created_after:
            query = query.where(RequestLogModel.created_at >= created_after)
        rows = (await self.session.execute(query)).all()
        return [(host, count) for host, count in rows]
