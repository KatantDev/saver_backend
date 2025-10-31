import re

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.task_manager.tasks import get_youtube_video_info, save_video
from saver_backend.telegram_bot.filters.source import SourceFilter
from saver_backend.telegram_bot.keyboards.callback import (
    VideoFormatCallback,
    VideoLanguageCallback,
)
from saver_backend.telegram_bot.keyboards.inline import (
    get_language_keyboard,
)

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


async def _trigger_download(
    query: CallbackQuery,
    resolution: Resolution,
    format_id: str,
) -> None:
    """
    Helper to delete the keyboard, send a confirmation, and start the download task.

    :param query: The callback query from the user's button press.
    :param resolution: The resolved URL information.
    :param format_id: The specific format ID to download.
    """
    if not query.message or not query.from_user:
        return

    # Delete the message with the format/language selection buttons
    if isinstance(query.message, Message):
        await query.message.delete()

    await save_video.kiq(
        resolution=resolution,
        telegram_id=query.from_user.id,
        format_id=format_id,
    )


@download_router.message(SourceFilter(sources=[SourceEnum.YOUTUBE_VIDEO_YDL]))
async def show_youtube_video_info(
    message: Message,
    resolution: Resolution,
) -> None:
    """
    Get info for a YouTube video, show thumbnail and format selection buttons.

    :param message: Message object.
    :param resolution: Resolution object.
    """
    if not message.from_user:
        return

    processing_message = await message.reply(_("get video info"))

    await get_youtube_video_info.kiq(
        resolution=resolution,
        telegram_id=message.from_user.id,
        processing_message_id=processing_message.message_id,
    )


@download_router.callback_query(VideoFormatCallback.filter())
async def on_format_select(
    query: CallbackQuery,
    callback_data: VideoFormatCallback,
    state: FSMContext,
) -> None:
    """
    Handle selection of a video resolution.

    If multiple languages are available for this resolution, it shows a
    language selection keyboard. Otherwise, it shows the final format info.
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    data = await state.get_value(key="video_dto")
    if not data:
        await query.message.edit_text(_("format selection expired"))
        return

    video_dto: VideoDTO = VideoDTO.model_validate(data)
    formats = video_dto.get_formats_by_label(label=callback_data.label)

    if len(formats) > 1:
        caption = _("choose language for download").format(
            title=video_dto.title,
            label=callback_data.label,
        )
        reply_markup = get_language_keyboard(formats)
        if query.message.caption:
            await query.message.edit_caption(caption=caption, reply_markup=reply_markup)
        else:
            await query.message.edit_text(caption, reply_markup=reply_markup)
    elif len(formats) == 1:
        selected_format = formats[0]
        if video_dto.url:
            resolution = Resolution(
                source=SourceEnum.YOUTUBE_VIDEO_YDL,
                url=video_dto.url,
            )
            await _trigger_download(
                query=query,
                resolution=resolution,
                format_id=selected_format.format_id,
            )
    else:
        await query.answer(_("format not found"), show_alert=True)
    await query.answer()


@download_router.callback_query(VideoLanguageCallback.filter())
async def on_language_select(
    query: CallbackQuery,
    callback_data: VideoLanguageCallback,
    state: FSMContext,
) -> None:
    """
    Handle the final selection of a specific format (with language).

    This handler fires after the user has selected a language,
    pinpointing the exact format to download.

    :param query: CallbackQuery object from the language selection.
    :param callback_data: Parsed callback data with the final format_id.
    :param state: State object from the callback data.
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    data = await state.get_data()
    video_dto_data = data.get("video_dto")

    if not video_dto_data:
        await query.message.edit_text(_("format selection expired"))
        return

    video_dto: VideoDTO = VideoDTO.model_validate(video_dto_data)
    selected_format = video_dto.get_format_by_id(format_id=callback_data.format_id)

    if not selected_format:
        await query.answer(_("format not found"), show_alert=True)
        return

    if video_dto.url:
        resolution = Resolution(
            source=SourceEnum.YOUTUBE_VIDEO_YDL,
            url=video_dto.url,
        )
        await _trigger_download(
            query=query,
            resolution=resolution,
            format_id=selected_format.format_id,
        )

    await query.answer()


@download_router.message(
    SourceFilter(
        sources=[
            SourceEnum.TIKTOK,
            SourceEnum.INSTAGRAM_YDL,
            SourceEnum.INSTAGRAM_API,
            SourceEnum.YOUTUBE_SHORTS_YDL,
            SourceEnum.VK_CLIPS_YDL,
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
