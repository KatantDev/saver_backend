import logging
from pathlib import Path

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis
from taskiq import TaskiqDepends

from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.exceptions import TikTokYtDlpDownloaderError
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.task_manager.state import DatabaseState, SaverState
from saver_backend.telegram_bot.keyboards.inline import get_video_formats_keyboard
from saver_backend.tkq import broker


@broker.task()
async def save_video(
    resolution: Resolution,
    telegram_id: int,
    state: SaverState = TaskiqDepends(),
    db: DatabaseState = TaskiqDepends(),
) -> None:
    """
    Save video.

    :param resolution: Resolution of the video.
    :param telegram_id: Telegram ID of the user.
    :param state: Saver state.
    :param db: Database state with DAOs.
    """
    logging.info("Resolving controller for %s", resolution)
    yt_dlp_controller = state.source_resolver.get_controller(resolution)
    if yt_dlp_controller is None:
        return

    message_id = await state.telegram_bot_controller.send_start_downloading(
        telegram_id=telegram_id,
        percent=0,
    )

    controller = yt_dlp_controller(
        resolution=resolution,
        telegram_bot_controller=state.telegram_bot_controller,
        telegram_id=telegram_id,
        message_id=message_id,
        video_cache_dao=db.video_cache_dao,
    )
    try:
        await controller.download_video()
    except TikTokYtDlpDownloaderError:
        await state.telegram_bot_controller.send_tiktok_error_downloading(
            telegram_id=telegram_id,
        )


@broker.task()
async def get_youtube_video_info(
    resolution: Resolution,
    telegram_id: int,
    chat_id: int,
    processing_message_id: int,
    state: SaverState = TaskiqDepends(),
    db: DatabaseState = TaskiqDepends(),
) -> None:
    """Fetches video info in the background and sends format selection to the user."""
    controller_class = state.source_resolver.get_controller(resolution)
    if not controller_class:
        return

    controller = controller_class(
        resolution=resolution,
        telegram_bot_controller=state.telegram_bot_controller,
        telegram_id=telegram_id,
        video_cache_dao=db.video_cache_dao,
    )

    info_dict = await controller.get_video_info(url=resolution.url)
    bot = state.telegram_bot_controller.bot

    if not info_dict:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=processing_message_id,
            text=_("failed to get video info"),
        )
        return

    video_dto = VideoDTO.from_yt_dlp(
        info=info_dict,
        file_path=Path("dummy"),
        thumbnail_path=None,
    )

    if not video_dto.formats:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=processing_message_id,
            text=_("No formats available for download."),
        )
        return

    redis_client = Redis.from_url(str(settings.redis_url))
    storage = RedisStorage(redis=redis_client)
    fsm_context = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=telegram_id),
    )

    try:
        await fsm_context.set_data({"video_dto": video_dto.model_dump(mode="json")})

        caption = _("<b>{title}</b>\n\nChoose desired quality:").format(
            title=video_dto.title,
        )
        reply_markup = get_video_formats_keyboard(video_dto)

        await bot.delete_message(chat_id, processing_message_id)

        if video_dto.thumbnail_url:
            await bot.send_photo(
                chat_id=chat_id,
                photo=video_dto.thumbnail_url,
                caption=caption,
                reply_markup=reply_markup,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                reply_markup=reply_markup,
            )
    finally:
        await redis_client.close()
