import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.types import Message

from saver_backend.db.dao.video_cache_dao import VideoCacheDAO
from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.resolver import SourceResolver
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.task_manager.tasks import save_video
from saver_backend.telegram_bot.filters.source import SourceFilter

if TYPE_CHECKING:
    from saver_backend.services.telegram.bot_controller import TelegramBotController


download_router = Router()


@download_router.message(SourceFilter(sources=[SourceEnum.UNSUPPORTED]))
async def send_unknown_url(
    message: Message,
    bot: Bot,
    resolution: Resolution,
) -> None:
    """
    Send unknown url.

    :param message: Message.
    :param bot: Bot.
    :param resolution: Resolution.
    """
    # Check if the url is a valid url
    pattern = re.compile(
        r"^(https?://)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(/[^\s]*)?$",
        re.IGNORECASE,
    )
    if not message.text or not pattern.match(message.text):
        return

    await message.answer(
        text=_("unknown url"),
    )
    await bot.send_message(
        chat_id=settings.admin_chat_id,
        text=f"Unsupported URL: {resolution.url}",
    )


@download_router.message(SourceFilter(sources=[SourceEnum.YOUTUBE_VIDEO_YDL]))
async def show_youtube_video_info(
    message: Message,
    resolution: Resolution,
    video_cache_dao: VideoCacheDAO,
    telegram_bot_controller: "TelegramBotController",
) -> None:
    """
    Get info for a YouTube video and display it.

    :param message: Message object.
    :param resolution: Resolution object.
    :param video_cache_dao: DAO for video cache.
    :param telegram_bot_controller: The main bot controller instance.
    """
    if not message.from_user:
        return

    resolver = SourceResolver()
    controller_class = resolver.get_controller(resolution)
    if not controller_class:
        return

    controller = controller_class(
        resolution=resolution,
        telegram_bot_controller=telegram_bot_controller,
        telegram_id=message.from_user.id,
        video_cache_dao=video_cache_dao,
    )

    info_dict = await controller.get_video_info(url=resolution.url)
    if not info_dict:
        await message.reply(_("failed to get video info"))
        return

    dummy_path = Path("dummy")
    video_dto = VideoDTO.from_yt_dlp(
        info=info_dict,
        file_path=dummy_path,
        thumbnail_path=None,
    )

    dto_json = json.dumps(
        video_dto.model_dump(exclude={"path", "thumbnail"}),
        indent=2,
        ensure_ascii=False,
    )
    response_text = f"<blockquote>{dto_json}</blockquote>"

    await message.reply(response_text, parse_mode=ParseMode.HTML)


@download_router.message(
    SourceFilter(
        sources=[
            SourceEnum.TIKTOK,
            SourceEnum.INSTAGRAM_YDL,
            SourceEnum.INSTAGRAM_API,
            SourceEnum.YOUTUBE_SHORTS_YDL,
        ],
    ),
)
async def download_video(message: Message, resolution: Resolution) -> None:
    """
    Download video from TikTok, Instagram, Instagram API.

    :param message: Message.
    :param resolution: Resolution.
    """
    if message.from_user is None:
        return

    await save_video.kiq(resolution=resolution, telegram_id=message.from_user.id)
