import sqlalchemy as sa
from sqlalchemy import select

from saver_backend.db.dao.base_dao import BaseDAO
from saver_backend.db.models.video_cache_model import VideoCacheModel
from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.schema import VideoCacheDTO


class VideoCacheDAO(BaseDAO):
    """Class for accessing the video_cache table."""

    async def create(
        self,
        video_cache: VideoCacheDTO,
    ) -> None:
        """
        Create a new video cache entry from a DTO.

        :param video_cache: A VideoCacheDTO containing all necessary cache information.
        """
        model = VideoCacheModel(
            source=video_cache.source,
            source_id=video_cache.source_id,
            file_id=video_cache.file_id,
            file_unique_id=video_cache.file_unique_id,
            quality=video_cache.quality,
            meta_data=video_cache.meta_data.model_dump(mode="json"),
        )
        self.session.add(model)
        await self.session.flush()

    async def get_by_source_id_and_quality(
        self,
        source: SourceEnum,
        source_id: str,
        quality: str,
    ) -> VideoCacheModel | None:
        """
        Get a video cache entry by its source, source_id, and quality.

        :param source: The source of the video.
        :param source_id: The unique ID from the source platform.
        :param quality: The specific quality/format_id of the video.
        :return: A VideoCacheModel instance or None if not found.
        """
        query = select(VideoCacheModel).where(
            VideoCacheModel.source == source,
            VideoCacheModel.source_id == source_id,
            VideoCacheModel.quality == quality,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_random(self, limit: int = 20) -> list[VideoCacheModel]:
        """
        Get N random video cache entries.

        :param limit: The maximum number of entries to return.
        :return: A list of VideoCacheModel instances.
        """
        query = select(VideoCacheModel).order_by(sa.func.random()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())
