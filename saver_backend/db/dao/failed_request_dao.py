from datetime import datetime

from sqlalchemy import func, select

from saver_backend.db.dao.base_dao import BaseDAO
from saver_backend.db.models.failed_request_model import FailedRequestModel
from saver_backend.entities.enums import SourceEnum


class FailedRequestDAO(BaseDAO):
    """Class for accessing the failed_request table."""

    async def create(
        self,
        telegram_id: int,
        source: SourceEnum,
        host: str,
        url: str,
        stage: str,
        reason: str,
        error: str | None = None,
    ) -> None:
        """
        Record one request the user got an error for.

        :param telegram_id: Telegram ID of the user.
        :param source: Resolved source.
        :param host: Host of the link.
        :param url: Full link the user sent.
        :param stage: Where it failed: video_info, download, inline, unsupported.
        :param reason: Kind of the error shown to the user.
        :param error: Last error logged while handling the request.
        """
        model = FailedRequestModel(
            telegram_id=telegram_id,
            source=source,
            host=host[:255],
            url=url[:2048],
            stage=stage,
            reason=reason,
            error=error,
        )
        self.session.add(model)
        await self._release()

    async def get_counts_by_source(
        self,
        created_after: datetime | None = None,
    ) -> list[tuple[SourceEnum, str, int]]:
        """
        Count failed requests by source and reason, most frequent first.

        :param created_after: Only count requests newer than this.
        :return: List of (source, reason, count).
        """
        count = func.count(FailedRequestModel.id)
        query = (
            select(FailedRequestModel.source, FailedRequestModel.reason, count)
            .group_by(FailedRequestModel.source, FailedRequestModel.reason)
            .order_by(count.desc())
        )
        if created_after:
            query = query.where(FailedRequestModel.created_at >= created_after)
        rows = (await self.session.execute(query)).all()
        return [(SourceEnum(source), reason, n) for source, reason, n in rows]
