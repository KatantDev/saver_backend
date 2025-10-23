from typing import Any

from sqlalchemy import select

from saver_backend.db.dao.base_dao import BaseDAO
from saver_backend.db.models.video_cache_model import VideoCacheModel
from saver_backend.entities.enums import SourceEnum


class VideoCacheDAO(BaseDAO):
    """Class for accessing the video_cache table."""

    async def create(
        self,
        source: SourceEnum,
        source_id: str,
        file_id: str,
        file_unique_id: str,
        meta_data: dict[str, Any] | None,
    ) -> None:
        """
        Create a new video cache entry.

        :param source: The source of the video.
        :param source_id: The unique ID from the source.
        :param file_id: The Telegram file_id.
        :param file_unique_id: The Telegram file_unique_id.
        :param meta_data: Additional metadata.
        """
        model = VideoCacheModel(
            source=source,
            source_id=source_id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            meta_data=meta_data,
        )
        self.session.add(model)
        await self.session.flush()

    async def get_by_source_id(
        self,
        source: SourceEnum,
        source_id: str,
    ) -> VideoCacheModel | None:
        """
        Get a video cache entry by its source and source_id.

        :param source: The source of the video.
        :param source_id: The unique ID from the source.
        :return: A VideoCacheModel instance or None if not found.
        """
        query = select(VideoCacheModel).where(
            VideoCacheModel.source == source,
            VideoCacheModel.source_id == source_id,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
