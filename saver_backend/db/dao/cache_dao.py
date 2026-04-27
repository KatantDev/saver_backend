from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from saver_backend.db.dao.base_dao import BaseDAO
from saver_backend.db.models.cache_model import CacheModel
from saver_backend.entities.enums import ContentTypeEnum, SourceEnum
from saver_backend.entities.mappers import DTO_TO_CONTENT_TYPE_MAP
from saver_backend.services.downloaders.schema import (
    CacheDTO,
)


class CacheDAO(BaseDAO):
    """Class for accessing the cache table."""

    async def create(
        self,
        cache_dto: CacheDTO,
    ) -> CacheModel:
        """
        Create a new cache entry from a DTO.

        :param cache_dto: A CacheDTO containing all necessary cache information.
        """
        content_type = DTO_TO_CONTENT_TYPE_MAP.get(type(cache_dto.meta_data))
        if not content_type:
            raise ValueError("Unsupported meta_data type for caching")

        model = CacheModel(
            source=cache_dto.source,
            source_id=cache_dto.source_id,
            file_id=cache_dto.file_id,
            file_unique_id=cache_dto.file_unique_id,
            quality=cache_dto.quality,
            content_type=content_type,
            meta_data=cache_dto.meta_data.model_dump(mode="json"),
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def get_by_filters(
        self,
        source: SourceEnum,
        source_id: str,
        quality: str,
        content_type: ContentTypeEnum | None = None,
    ) -> CacheModel | None:
        """
        Get a cache entry by a set of filters.

        :param source: The source of the content.
        :param source_id: The unique ID from the source platform.
        :param quality: The specific quality/format_id of the content.
        :param content_type: The type of content to look for.
        :return: A CacheModel instance or None if not found.
        """
        query = select(CacheModel).where(
            CacheModel.source == source,
            CacheModel.source_id == source_id,
            CacheModel.quality == quality,
        )
        if content_type:
            query = query.where(CacheModel.content_type == content_type)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_latest(
        self,
        limit: int = 20,
        sources: list[SourceEnum] | None = None,
        content_types: list[ContentTypeEnum] | None = None,
    ) -> list[CacheModel]:
        """
        Get the latest N cache entries.

        :param limit: The maximum number of entries to return.
        :param sources: Optional list of sources to filter by.
        :param content_types: Optional list of content types to filter by.
        :return: A list of CacheModel instances.
        """
        query = select(CacheModel).order_by(CacheModel.created_at.desc())

        if sources:
            query = query.where(CacheModel.source.in_(sources))
        if content_types:
            query = query.where(CacheModel.content_type.in_(content_types))

        query = query.limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_or_create(
        self,
        cache_dto: CacheDTO,
    ) -> CacheModel | None:
        """
        Update existing cache entry or create a new one.

        If a record with the same (source, source_id, quality) exists,
        updates its meta_data and updated_at fields.
        Otherwise creates a new record.

        Uses PostgreSQL's ON CONFLICT DO UPDATE for atomic operation.

        :param cache_dto: The cache DTO containing all necessary information.
        :return: The created or updated CacheModel instance.
        """
        content_type = DTO_TO_CONTENT_TYPE_MAP.get(type(cache_dto.meta_data))
        if not content_type:
            raise ValueError("Unsupported meta_data type for caching")

        stmt = insert(CacheModel).values(
            source=cache_dto.source,
            source_id=cache_dto.source_id,
            file_id=cache_dto.file_id,
            file_unique_id=cache_dto.file_unique_id,
            quality=cache_dto.quality,
            content_type=content_type,
            meta_data=cache_dto.meta_data.model_dump(mode="json", exclude_unset=True),
        )

        # ON CONFLICT: update meta_data and updated_at
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_id", "quality"],
            set_={
                "meta_data": stmt.excluded.meta_data,
                "updated_at": func.now(),
            },
        )

        returning_stmt = stmt.returning(CacheModel)

        result = await self.session.execute(returning_stmt)
        await self.session.flush()
        return result.scalar_one_or_none()
