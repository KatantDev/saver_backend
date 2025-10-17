import logging
import re

from aiogram import Bot, Router
from aiogram.types import Message

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.task_manager.tasks import save_video
from saver_backend.telegram_bot.filters.source import SourceFilter

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
    if not pattern.match(message.text or ""):
        return

    logging.info(f"[TG] User {message.from_user.id if message.from_user else 'unknown'} sent unknown url: {getattr(resolution, 'url', None)} | text: {message.text}")
    await message.answer(
        text=_("unknown url"),
    )
    await bot.send_message(
        chat_id=settings.admin_chat_id,
        text=f"Unsupported URL: {resolution.url}",
    )


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
    logging.info(f"[TG] User {message.from_user.id} requested download. Text: {message.text}, Resolution: {resolution}")
    await save_video.kiq(resolution=resolution, telegram_id=message.from_user.id)


@download_router.message(SourceFilter(sources=[SourceEnum.VK]))
async def handle_vk_direct_download(message: Message, resolution: Resolution) -> None:
    """
    Handle VK video - redirect to VK preview handler.
    This handler is a fallback in case VK handler doesn't catch the message.

    :param message: Message.
    :param resolution: Resolution.
    """
    if message.from_user is None:
        return
    
    logging.info(f"[TG] VK Fallback Handler called! User {message.from_user.id} sent VK URL: {resolution.url}")
    logging.info(f"[TG] Resolution source: {resolution.source}, metadata: {resolution.metadata}")
    
    await message.answer(
        f"URL: {resolution.url}\n"
        f"Source: {resolution.source}\n"
        f"Пожалуйста, используйте команду /start и отправьте ссылку заново."
    )
