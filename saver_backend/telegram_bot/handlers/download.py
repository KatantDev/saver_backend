import json
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


@download_router.message(SourceFilter(sources=[SourceEnum.YOUTUBE_VIDEO_YDL]))
async def show_youtube_video_info(
    message: Message,
    resolution: Resolution,
) -> None:
    """
    Get info for a YouTube video, show thumbnail and format selection buttons.

    :param message: Message object.
    :param resolution: Resolution object.
    :param video_cache_dao: DAO for video cache.
    :param telegram_bot_controller: The main bot controller instance.
    :param redis: Redis client instance.
    """
    if not message.from_user:
        return

    processing_message = await message.reply(_("🔍 Getting video info..."))

    await get_youtube_video_info.kiq(
        resolution=resolution,
        telegram_id=message.from_user.id,
        chat_id=message.chat.id,
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

    data = await state.get_data()
    video_dto_data = data.get("video_dto")

    if not video_dto_data:
        await query.message.edit_text(
            _("Selection time expired. Please send the link again."),
        )
        return

    video_dto = VideoDTO.model_validate(video_dto_data)
    formats_for_label = video_dto.unique_formats_by_label.get(callback_data.label, [])

    if len(formats_for_label) > 1:
        caption = _(
            "<b>{title}</b>\n\nMultiple audio tracks are available for quality {label}."
            " Please select the language:",
        ).format(
            title=video_dto.title,
            label=callback_data.label,
        )
        reply_markup = get_language_keyboard(formats_for_label)
        if query.message.caption:
            await query.message.edit_caption(caption=caption, reply_markup=reply_markup)
        else:
            await query.message.edit_text(caption, reply_markup=reply_markup)
    elif len(formats_for_label) == 1:
        format_dto = formats_for_label[0]
        video_dump = json.dumps(
            video_dto.model_dump(exclude={"formats"}),
            indent=2,
            ensure_ascii=False,
        )
        format_dump = json.dumps(format_dto.model_dump(), indent=2, ensure_ascii=False)
        text = (
            f"<blockquote>{video_dump}</blockquote>\n\n<b>{_('Selected format:')}</b>\n"
            f"<blockquote>{format_dump}</blockquote>"
        )
        if query.message.caption:
            await query.message.edit_caption(caption=text)
        else:
            await query.message.edit_text(text)
    else:
        await query.answer(
            _("An error occurred, the format was not found."),
            show_alert=True,
        )
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
    :param redis: Redis client instance.
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    data = await state.get_data()
    video_dto_data = data.get("video_dto")

    if not video_dto_data:
        await query.message.edit_text(
            _("Selection time expired. Please send the link again."),
        )
        return

    video_dto = VideoDTO.model_validate(video_dto_data)
    selected_format = next(
        (fmt for fmt in video_dto.formats if fmt.format_id == callback_data.format_id),
        None,
    )

    if not selected_format:
        await query.answer(
            _("An error occurred, the format was not found."),
            show_alert=True,
        )
        return

    video_dump = json.dumps(
        video_dto.model_dump(exclude={"formats"}),
        indent=2,
        ensure_ascii=False,
    )
    format_dump = json.dumps(selected_format.model_dump(), indent=2, ensure_ascii=False)
    text = (
        f"<blockquote>{video_dump}</blockquote>\n\n<b>{_('Selected format:')}</b>\n"
        f"<blockquote>{format_dump}</blockquote>"
    )

    if query.message.caption:
        await query.message.edit_caption(caption=text)
    else:
        await query.message.edit_text(text)

    await query.answer()


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
