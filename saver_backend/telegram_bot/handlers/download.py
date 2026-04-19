import json
import logging
import re
from contextlib import suppress
from typing import Any

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.task_manager.tasks import (
    get_video_info,
    save_video,
)
from saver_backend.telegram_bot.filters.source import SourceFilter
from saver_backend.telegram_bot.keyboards.callback import (
    VideoFormatCallback,
    VideoLanguageCallback,
    VideoSeasonCallback,
    VideoSeriesCallback,
    VideoTranslationCallback,
)
from saver_backend.telegram_bot.keyboards.inline import (
    get_language_keyboard,
    get_season_keyboard,
    get_series_keyboard,
    get_translations_keyboard,
    get_video_formats_keyboard,
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
    pattern = re.compile(r"(https?://\S+)", re.IGNORECASE)
    if not message.text or not pattern.match(resolution.url):
        logging.info("User sent message: %s", message.text)
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
    format_id: str | None,
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
        with suppress(TelegramBadRequest):
            await query.message.delete()

    await save_video.kiq(
        resolution=resolution,
        telegram_id=query.from_user.id,
        format_id=format_id,
    )


@download_router.message(
    SourceFilter(
        sources=[
            SourceEnum.YOUTUBE_VIDEO_YDL,
            SourceEnum.VK_VIDEO_YDL,
            SourceEnum.RUTUBE_YDL,
            SourceEnum.M3U8_YDL,
            SourceEnum.OK_YDL,
            SourceEnum.KINOVOD_YDL,
        ],
    ),
)
async def show_video_info(
    message: Message,
    resolution: Resolution,
    state: FSMContext,
    bot: Bot,
) -> None:
    """
    Get info for a video, show thumbnail and format selection buttons.

    Also, cleans up previous selection keyboards if any exist.

    :param message: Message object.
    :param resolution: Resolution object.
    :param state: FSM context.
    :param bot: Bot instance.
    """
    if not message.from_user:
        return

    data = await state.get_data()
    previous_message_id = data.get("quality_selection_message_id")

    if previous_message_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=message.from_user.id,
                message_id=previous_message_id,
                reply_markup=None,
            )
        except TelegramBadRequest:
            pass
        finally:
            await state.clear()

    processing_message = await message.reply(_("get video info"))

    await get_video_info.kiq(
        resolution=resolution,
        telegram_id=message.from_user.id,
        processing_message_id=processing_message.message_id,
    )


@download_router.callback_query(VideoFormatCallback.filter())
async def on_format_select(  # noqa: PLR0915, PLR0912, C901
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
    resolution_data = data.get("resolution")
    info_dict_fsm = json.loads(data.get("info_dict", "{}"))
    videotheatre_dict = info_dict_fsm.get("seasons")
    info_dict = videotheatre_dict.get("info_dict")
    lang = "en"  # todo from info_dict
    if not video_dto_data or not resolution_data:
        await query.message.edit_text(_("format selection expired"), reply_markup=None)
        return

    video_dto: VideoDTO = VideoDTO.model_validate(video_dto_data)
    resolution: Resolution = Resolution.model_validate(resolution_data)
    formats = video_dto.get_formats_by_label(label=callback_data.label)

    if "seasons" in info_dict_fsm:
        seasons = info_dict["seasons"]
        caption: str | None = None
        data_to_fsm = {"quality_label": callback_data.label}
        reply_markup: InlineKeyboardMarkup | None = None
        video_dto.quality = callback_data.label
        if len(seasons) > 1:
            caption, reply_markup = choose_season(video_dto, seasons)
        else:
            episodes = seasons[0]["folder"]
            translations = seasons[0]["folder"][0]["file"][callback_data.label]
            if len(translations) > 1:
                caption, reply_markup = choose_translations(
                    video_dto,
                    seasons[0]["title"],
                    translations,
                    lang,
                )
            elif len(episodes) > 1:
                caption, reply_markup = choose_series(
                    video_dto,
                    seasons[0]["title"],
                    info_dict["perevod_from_html"],
                    series_list=episodes,
                )
            else:
                await _trigger_download(
                    query=query,
                    resolution=resolution,
                    format_id=None,
                )

        await state.update_data(data=data_to_fsm)
        if caption and reply_markup:
            if query.message.caption:
                await query.message.edit_caption(
                    caption=caption,
                    reply_markup=reply_markup,
                )
            else:
                await query.message.edit_text(caption, reply_markup=reply_markup)
    elif len(formats) > 1:
        caption = _("choose language for download").format(
            title=video_dto.title_html,
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
            await _trigger_download(
                query=query,
                resolution=resolution,
                format_id=selected_format.format_id,
            )
    else:
        await query.answer(_("format selection expired"), show_alert=True)
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
    resolution_data = data.get("resolution")

    if not video_dto_data or not resolution_data:
        await query.message.edit_text(
            _("format selection expired"),
            reply_markup=None,
        )
        return

    video_dto: VideoDTO = VideoDTO.model_validate(video_dto_data)
    resolution: Resolution = Resolution.model_validate(resolution_data)
    selected_format = video_dto.get_format_by_id(format_id=callback_data.format_id)

    if not selected_format:
        await query.answer(_("format selection expired"), show_alert=True)
        return

    if video_dto.url:
        await _trigger_download(
            query=query,
            resolution=resolution,
            format_id=selected_format.format_id,
        )

    await query.answer()


@download_router.callback_query(VideoSeasonCallback.filter())
async def on_seasons_select(  # noqa: C901
    query: CallbackQuery,
    callback_data: VideoSeasonCallback,
    state: FSMContext,
) -> None:
    """
    Handle selection of a season.

    If multiple translations are available for this season, it shows a
    translation selection keyboard. Otherwise, it shows the series selection.
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    data = await state.get_data()
    video_dto_data = data.get("video_dto")
    resolution_data = data.get("resolution")
    info_dict_fsm = json.loads(data.get("info_dict", "{}"))
    videotheatre_dict = info_dict_fsm.get("seasons")
    info_dict = videotheatre_dict.get("info_dict")

    video_dto: VideoDTO = VideoDTO.model_validate(video_dto_data)
    resolution: Resolution = Resolution.model_validate(resolution_data)

    caption = None
    reply_markup: InlineKeyboardMarkup | None = None
    lang = "en"  # todo lang from info dict
    _data: dict[str, Any] | None = None
    if callback_data.label == "back_to_formats":
        # Handle back button
        caption, reply_markup = choose_quality(video_dto, lang)
    elif "quality_label" in data:
        season_label = callback_data.label
        video_dto.quality = data["quality_label"]
        _data = {"season_label": season_label}
        season = get_season(info_dict["seasons"], season_label)
        translations = season["folder"][0]["file"][info_dict["qualities"][0]]
        if len(translations) > 1:
            # Sending translations selection message
            caption, reply_markup = choose_translations(
                video_dto,
                season_label=season_label,
                translations=info_dict["translations"],
                lang=lang,
            )

        elif len(translations) == 1:
            translation_key = next(iter(translations.keys()))
            _data["translation_label"] = translation_key
            series_data = season["folder"]
            if isinstance(series_data, list):
                if len(series_data) > 1:
                    # Multiple series available
                    caption, reply_markup = choose_series(
                        video_dto,
                        season_label=season_label,
                        translation_name=info_dict["perevod_from_html"],
                        series_list=series_data,
                    )
                elif len(series_data) == 1:
                    _data["episode_label"] = "1 серия"
                    await _trigger_download(
                        query=query,
                        resolution=resolution,
                        format_id=None,
                    )

    if caption and reply_markup:
        if _data:
            await state.update_data(data=_data)
        if query.message.caption:
            await query.message.edit_caption(caption=caption, reply_markup=reply_markup)
        else:
            await query.message.edit_text(caption, reply_markup=reply_markup)
    await query.answer()


@download_router.callback_query(VideoTranslationCallback.filter())
async def on_translations_select(
    query: CallbackQuery,
    callback_data: VideoTranslationCallback,
    state: FSMContext,
) -> None:
    """
    Handle selection of a translation for a specific season.

    Shows series selection keyboard for the chosen translation,
    or triggers download if it's a movie (single series).

    :param query: CallbackQuery object from the translation selection.
    :param callback_data: Parsed callback data with translation info.
    :param state: FSM context.
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    data = await state.get_data()
    video_dto_data = data.get("video_dto")
    resolution_data = data.get("resolution")
    info_dict_fsm = json.loads(data.get("info_dict", "{}"))
    videotheatre_dict = info_dict_fsm.get("seasons")
    info_dict = videotheatre_dict.get("info_dict")

    if not video_dto_data or not resolution_data:
        await query.message.edit_text(
            _("selection expired"),
            reply_markup=None,
        )
        return

    video_dto: VideoDTO = VideoDTO.model_validate(video_dto_data)
    resolution: Resolution = Resolution.model_validate(resolution_data)

    # Handle back button
    if callback_data.label == "back_to_season":
        # Handle back button
        await on_format_select(
            query,
            VideoFormatCallback(label=data["quality_label"]),
            state=state,
        )
    # Get series data for the selected translation
    quality_label = data.get("quality_label", "")
    season_label = data.get("season_label", "")
    translation_label = callback_data.label

    if not quality_label or not season_label:
        await query.answer(_("selection expired"), show_alert=True)
        return

    try:
        season = get_season(info_dict["seasons"], season_label)
        series_data = season["folder"]
    except KeyError:
        await query.answer(_("selection expired"), show_alert=True)
        return

    # Store translation selection
    await state.update_data(
        {
            "translation_label": translation_label,
        },
    )

    # Check if it's a series (multiple episodes) or a movie (single)
    if isinstance(series_data, list) and len(series_data) > 1:
        # Multiple series available
        video_dto.quality = quality_label
        caption, reply_markup = choose_series(
            video_dto,
            season_label=season_label,
            translation_name=info_dict["translations"][translation_label],
            series_list=series_data,
        )

        if query.message.caption:
            await query.message.edit_caption(caption=caption, reply_markup=reply_markup)
        else:
            await query.message.edit_text(caption, reply_markup=reply_markup)
    else:
        # Single series/movie - ask if download all or just this one
        await _trigger_download(
            query=query,
            resolution=resolution,
            format_id=None,
        )

    await query.answer()


@download_router.callback_query(VideoSeriesCallback.filter())
async def on_series_select(
    query: CallbackQuery,
    callback_data: VideoSeriesCallback,
    state: FSMContext,
) -> None:
    """
    Handle selection of a specific series or downloading all series.

    :param query: CallbackQuery object from the series selection.
    :param callback_data: Parsed callback data with series info.
    :param state: FSM context.
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    data = await state.get_data()
    video_dto_data = data.get("video_dto")
    resolution_data = data.get("resolution")
    quality_label = data.get("quality_label")
    season_label = data.get("season_label")
    info_dict_fsm = json.loads(data.get("info_dict", "{}"))
    videotheatre_dict = info_dict_fsm.get("seasons")
    info_dict = videotheatre_dict.get("info_dict")

    if not video_dto_data or not resolution_data:
        await query.message.edit_text(
            _("selection expired"),
            reply_markup=None,
        )
        return

    video_dto: VideoDTO = VideoDTO.model_validate(video_dto_data)  # todo del
    resolution: Resolution = Resolution.model_validate(resolution_data)
    seasons = info_dict["seasons"]
    season = get_season(seasons, season_label or "")
    translations = season["folder"][0]["file"][info_dict["qualities"][0]]
    # Handle back button
    if callback_data.label == "back_to_translations":
        if len(translations) > 1:
            video_dto.quality = quality_label
            caption, reply_markup = choose_translations(
                video_dto,
                season_label=season_label or "",
                translations=info_dict["translations"],
            )
        elif len(seasons) > 1:
            video_dto.quality = quality_label
            caption, reply_markup = choose_season(
                video_dto,
                seasons=seasons,
            )
        else:
            caption, reply_markup = choose_quality(
                video_dto,
                lang="en",  # todo lang from info dict
            )
        if query.message.caption:
            await query.message.edit_caption(caption=caption, reply_markup=reply_markup)
        else:
            await query.message.edit_text(caption, reply_markup=reply_markup)
        await query.answer()
        return

    await state.update_data(data={"episode_label": callback_data.label})
    # Download single series
    await _trigger_download(
        query=query,
        resolution=resolution,
        format_id=None,
    )

    await query.answer()


@download_router.message(
    SourceFilter(
        sources=[
            SourceEnum.TIKTOK,
            SourceEnum.INSTAGRAM_INDOWN,
            SourceEnum.YOUTUBE_SHORTS_YDL,
            SourceEnum.VK_CLIPS_YDL,
            SourceEnum.PINTEREST_YDL,
            SourceEnum.X_YDL,
            SourceEnum.DZEN_YDL,
            SourceEnum.ADULT_YDL,
            SourceEnum.FACEBOOK_YDL,
            SourceEnum.VK_API_YDL,
            SourceEnum.YMDANTIC,
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


def choose_season(
    video_dto: VideoDTO,
    seasons: list[dict[str, Any]],
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Generate caption and keyboard for season selection.

    :param video_dto: Video DTO containing video information
    :param seasons_keys: List of season keys/numbers
    :return: Tuple of (caption text, inline keyboard markup)
    """
    caption = _("choose season").format(title=video_dto.title_html)

    reply_markup = get_season_keyboard([item["title"] for item in seasons])
    return caption, reply_markup


def choose_language(
    video_dto: VideoDTO,
    label: str,
    formats: list[Any],
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Generate caption and keyboard for language selection.

    :param video_dto: Video DTO containing video information
    :param label: Quality/format label
    :param formats: List of available formats with languages
    :return: Tuple of (caption text, inline keyboard markup)
    """
    caption = _("choose language for download").format(
        title=video_dto.title_html,
        label=label,
    )
    reply_markup = get_language_keyboard(formats)
    return caption, reply_markup


def choose_quality(
    video_dto: VideoDTO,
    lang: str = "en",
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Generate caption and keyboard for quality selection.

    :param video_dto: Video DTO containing video information
    :param lang: Language code for localization
    :return: Tuple of (caption text, inline keyboard markup)
    """
    caption = _("choose quality", locale=lang).format(title=video_dto.title_html)
    reply_markup = get_video_formats_keyboard(labels=video_dto.unique_labels)
    return caption, reply_markup


def choose_translations(
    video_dto: VideoDTO,
    season_label: str,
    translations: dict[str, Any],
    lang: str = "en",
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Generate caption and keyboard for translation selection.

    :param video_dto: Video DTO containing video information
    :param season_label: Season number/label
    :param translations: Dictionary of available translations
    :param lang: Language code for localization
    :return: Tuple of (caption text, inline keyboard markup)
    """
    caption = _("choose translations", locale=lang).format(
        title=video_dto.title_html,
        season=season_label,
    )
    reply_markup = get_translations_keyboard(translations=translations)
    return caption, reply_markup


def choose_series(
    video_dto: VideoDTO,
    season_label: str,
    translation_name: str,
    series_list: list[dict[str, Any]],
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Generate caption and keyboard for series/episode selection.

    :param video_dto: Video DTO containing video information
    :param season_label: Season number/label
    :param translation_name: Name of the translation/dubbing
    :param series_list: List of available series/episodes
    :return: Tuple of (caption text with normalized line breaks, inline keyboard markup)
    """
    caption = _("choose series").format(
        title=video_dto.title_html,
        season=f"› {season_label}" if season_label else "",  # noqa: RUF001
        translation=f"› {translation_name}" if translation_name else "",  # noqa: RUF001
    )
    caption = re.sub(r"\n{3,}", "\n\n", caption)
    reply_markup = get_series_keyboard(series_list=series_list)
    return caption, reply_markup


def get_season(seasons: list[dict[str, Any]], season_label: str) -> dict[str, Any]:
    """Return season by season_label."""
    return next(item for item in seasons if item["title"] == season_label)
