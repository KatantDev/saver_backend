from uuid import uuid4

from aiogram import F, Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedMpeg4Gif,
    InputTextMessageContent,
)

from saver_backend.db.dao.cache_dao import CacheDAO
from saver_backend.entities.enums import ContentTypeEnum, SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.i18n import gettext as _
from saver_backend.task_manager.tasks import process_inline_query
from saver_backend.telegram_bot.filters.source import SourceFilter

inline_router = Router()


@inline_router.inline_query(F.query == "")
async def on_empty_inline_query(
    query: InlineQuery,
    cache_dao: CacheDAO,
) -> None:
    """
    Handle empty inline query by showing latest cached videos.

    :param query: The inline query object.
    :param cache_dao: DAO for accessing cache.
    """
    cached_items = await cache_dao.get_latest(
        limit=20,
        sources=[
            SourceEnum.TIKTOK,
            SourceEnum.INSTAGRAM_YDL,
            SourceEnum.VK_CLIPS_YDL,
            SourceEnum.YOUTUBE_SHORTS_YDL,
        ],
        content_types=[ContentTypeEnum.VIDEO],
    )

    results: list[InlineQueryResultCachedMpeg4Gif] = []
    for item in cached_items:
        video_dto = item.meta_data_dto
        if not isinstance(video_dto, VideoDTO):
            continue

        results.append(
            InlineQueryResultCachedMpeg4Gif(
                id=str(uuid4()),
                mpeg4_file_id=item.file_id,
                caption=_(
                    "result direct message",
                ).format(url=video_dto.url),
            ),
        )

    await query.answer(
        results=results,  # type: ignore[arg-type]
        cache_time=0,
        is_personal=True,
    )


@inline_router.inline_query(
    SourceFilter(
        sources=[
            SourceEnum.TIKTOK,
            SourceEnum.INSTAGRAM_YDL,
        ],
    ),
)
async def on_inline_query(query: InlineQuery, resolution: Resolution) -> None:
    """
    Handle inline queries for downloading videos.

    :param query: The inline query object.
    :param resolution: The resolved URL information.
    """
    await process_inline_query.kiq(
        resolution=resolution,
        telegram_id=query.from_user.id,
        inline_query_id=query.id,
    )


@inline_router.inline_query()
async def on_inline_query_fallback(
    query: InlineQuery,
) -> None:
    """
    Handle inline queries with unknown url or text.

    :param query: The inline query object.
    """
    result = InlineQueryResultArticle(
        id=str(uuid4()),
        title=_("unsupported inline query"),
        description=_("supported inline query urls"),
        input_message_content=InputTextMessageContent(
            message_text=_("unsupported inline query"),
        ),
    )
    await query.answer(results=[result], cache_time=0)
