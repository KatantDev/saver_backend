import contextlib
import logging
import re
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)

from saver_backend.entities.enums import (
    ContentTypeEnum,
    SourceEnum,
)
from saver_backend.entities.enums import (
    KeyboardBacksEnum as Back,
)
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.schema import VideoDTO, VideoTheatreDTO
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.task_manager.tasks import (
    get_video_info,
    save_video,
)
from saver_backend.telegram_bot.filters.source import SourceFilter
from saver_backend.telegram_bot.keyboards.callback import (
    VideoEpisodesCallback,
    VideoFormatCallback,
    VideoLanguageCallback,
    VideoSeasonCallback,
    VideoTranslationCallback,
    YmdanticFlacCallback,
)
from saver_backend.telegram_bot.keyboards.inline import (
    edit_telegram_message_keyboard,
    get_language_keyboard,
)
from saver_backend.telegram_bot.keyboards.videotheatre import (
    check_fsm_data,
    choose_episodes,
    choose_quality,
    choose_season,
    choose_translations,
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


@download_router.callback_query(
    VideoFormatCallback.filter(F.contenttype == ContentTypeEnum.FILM_DICT),
)
async def on_format_select_for_videotheatre(
    query: CallbackQuery,
    callback_data: VideoFormatCallback,
    state: FSMContext,
) -> None:
    """
    Handle selection of a video resolution for VideoTheatreDTO.

    If multiple seasons are available for this resolution, it shows a
    seasons selection keyboard. Otherwise, it shows the episodes keyboard.
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    fsm_data = await state.get_data()
    video_dto_data = fsm_data.get("video_dto")
    resolution_data = fsm_data.get("resolution")

    if not await check_fsm_data(
        query=query,
        fsm_data=fsm_data,
        message="format selection expired",
    ):
        return

    video_dto: VideoDTO = VideoDTO.model_validate(video_dto_data)
    resolution: Resolution = Resolution.model_validate(resolution_data)

    fsm_data["quality_label"] = callback_data.label
    videotheatre_dto = VideoTheatreDTO.from_fsm_data(fsm_data, resolution)

    seasons = videotheatre_dto.seasons
    caption: str | None = None
    data_to_fsm = {"quality_label": callback_data.label}
    reply_markup: InlineKeyboardMarkup | None = None
    video_dto.quality = callback_data.label

    if len(seasons) > 1:
        caption, reply_markup = choose_season(video_dto.title_html, seasons)
    else:
        selected_season = videotheatre_dto.selected_season
        data_to_fsm["season_label"] = selected_season.label
        video_dto.season = videotheatre_dto.selected_season_title

        if len(selected_season.episodes) > 1:
            caption, reply_markup = choose_episodes(
                title_html=video_dto.title_html,
                episodes_list=selected_season.episodes,
            )
        elif len(videotheatre_dto.available_translations) > 1:
            data_to_fsm["translation_label"] = next(
                iter(videotheatre_dto.available_translations),
            )
            caption, reply_markup = choose_translations(
                title_html=video_dto.title_html,
                translations=videotheatre_dto.available_translations,
            )
        else:
            video_dto.episode = videotheatre_dto.selected_episode.title
            data_to_fsm["translation_label"] = next(
                iter(videotheatre_dto.available_translations),
            )
            await _trigger_download(
                query=query,
                resolution=resolution,
                format_id=None,
            )

    await state.update_data(data=data_to_fsm)

    if caption and reply_markup:
        await edit_telegram_message_keyboard(query, caption, reply_markup)

    await query.answer()


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
    resolution_data = data.get("resolution")

    if not video_dto_data or not resolution_data:
        await query.message.edit_text(_("format selection expired"), reply_markup=None)
        return

    video_dto: VideoDTO = VideoDTO.model_validate(video_dto_data)
    resolution: Resolution = Resolution.model_validate(resolution_data)
    formats = video_dto.get_formats_by_label(label=callback_data.label)

    if len(formats) > 1:
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
async def on_seasons_select(
    query: CallbackQuery,
    callback_data: VideoSeasonCallback,
    state: FSMContext,
) -> None:
    """
    Handle selection of a season.

    If multiple episodes are available for this season, it shows an
    episodes selection keyboard. Otherwise, it shows the translation selection.
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    fsm_data = await state.get_data()
    video_dto_data = fsm_data.get("video_dto")
    resolution_data = fsm_data.get("resolution")

    if not await check_fsm_data(
        query=query,
        fsm_data=fsm_data,
        message="selection expired",
    ):
        return

    video_dto: VideoDTO = VideoDTO.model_validate(video_dto_data)
    resolution: Resolution = Resolution.model_validate(resolution_data)

    caption = None
    reply_markup: InlineKeyboardMarkup | None = None
    lang = "en"  # todo lang from info dict

    if callback_data.label != Back.TO_FORMATS:
        fsm_data["season_label"] = callback_data.label
    videotheatre_dto = VideoTheatreDTO.from_fsm_data(fsm_data, resolution)

    if callback_data.label == Back.TO_FORMATS:
        data_to_fsm = {"season_label": "", "episode_label": "", "translation_label": ""}
        # Handle back button
        caption, reply_markup = choose_quality(
            title_html=video_dto.title_html,
            lables=video_dto.unique_labels,
            contenttype=ContentTypeEnum.FILM_DICT,
            lang=lang,
        )
    else:
        season_label = callback_data.label
        video_dto.quality = videotheatre_dto.quality_real
        data_to_fsm = {"season_label": season_label}

        selected_season = videotheatre_dto.selected_season
        video_dto.season = videotheatre_dto.selected_season_title
        if len(selected_season.episodes) > 1:
            # Sending episodes selection message
            caption, reply_markup = choose_episodes(
                title_html=video_dto.title_html,
                episodes_list=selected_season.episodes,
            )

        elif len(videotheatre_dto.available_translations) > 1:
            video_dto.episode = videotheatre_dto.selected_episode_title

            data_to_fsm["episode_label"] = videotheatre_dto.selected_episode.label

            caption, reply_markup = choose_translations(
                title_html=video_dto.title_html,
                translations=videotheatre_dto.available_translations,
            )
        else:
            translation_key = next(iter(videotheatre_dto.available_translations))
            data_to_fsm["episode_label"] = videotheatre_dto.selected_episode.label
            data_to_fsm["translation_label"] = translation_key

            await _trigger_download(
                query=query,
                resolution=resolution,
                format_id=None,
            )
    if data_to_fsm:
        await state.update_data(data=data_to_fsm)
    if caption and reply_markup:
        await edit_telegram_message_keyboard(query, caption, reply_markup)
    await query.answer()


@download_router.callback_query(VideoEpisodesCallback.filter())
async def on_episodes_select(
    query: CallbackQuery,
    callback_data: VideoEpisodesCallback,
    state: FSMContext,
) -> None:
    """
    Handle selection of a specific episode.

    :param query: CallbackQuery object from the episode selection.
    :param callback_data: Callback data for filter to episodes keyboard .
    :param state: FSM context.
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    fsm_data = await state.get_data()
    video_dto_data = fsm_data.get("video_dto")
    resolution_data = fsm_data.get("resolution")

    if not await check_fsm_data(
        query=query,
        fsm_data=fsm_data,
        message="selection expired",
    ):
        return

    video_dto: VideoDTO = VideoDTO.model_validate(video_dto_data)
    resolution: Resolution = Resolution.model_validate(resolution_data)

    if callback_data.label != Back.TO_SEASONS:
        fsm_data["episode_label"] = callback_data.label
    videotheatre_dto = VideoTheatreDTO.from_fsm_data(fsm_data, resolution)

    available_translations = videotheatre_dto.available_translations

    if not videotheatre_dto.seasons:
        await query.answer()
        return

    # Handle back button
    if callback_data.label == Back.TO_SEASONS:
        await state.update_data(
            data={"season_label": "", "episode_label": "", "translation_label": ""},
        )
        if len(videotheatre_dto.seasons) > 1:
            video_dto.quality = videotheatre_dto.quality

            caption, reply_markup = choose_season(
                title_html=video_dto.title_html,
                seasons=videotheatre_dto.seasons,
            )
        else:
            caption, reply_markup = choose_quality(
                title_html=video_dto.title_html,
                lables=video_dto.unique_labels,
                contenttype=ContentTypeEnum.FILM_DICT,
            )

        await edit_telegram_message_keyboard(query, caption, reply_markup)
    elif len(videotheatre_dto.available_translations) > 1:
        await state.update_data(data={"episode_label": callback_data.label})
        video_dto.quality = videotheatre_dto.quality_real
        video_dto.season = videotheatre_dto.selected_season_title
        video_dto.episode = videotheatre_dto.selected_episode_title

        caption, reply_markup = choose_translations(
            title_html=video_dto.title_html,
            translations=available_translations,
        )
        await edit_telegram_message_keyboard(query, caption, reply_markup)

    else:
        await state.update_data(
            data={
                "episode_label": callback_data.label,
                "translation_label": next(iter(available_translations)),
            },
        )
        # Download single series
        await _trigger_download(
            query=query,
            resolution=resolution,
            format_id=None,
        )

    await query.answer()


@download_router.callback_query(VideoTranslationCallback.filter())
async def on_translations_select(
    query: CallbackQuery,
    callback_data: VideoTranslationCallback,
    state: FSMContext,
) -> None:
    """
    Handle selection of a translation for a specific episode.

    Triggers download.

    :param query: CallbackQuery object from the translation selection.
    :param callback_data: Callback data for filter to translation keyboard.
    :param state: FSM context.
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    fsm_data = await state.get_data()
    video_dto_data = fsm_data.get("video_dto")
    resolution_data = fsm_data.get("resolution")

    if not await check_fsm_data(query, fsm_data, message="selection expired"):
        return

    video_dto: VideoDTO = VideoDTO.model_validate(video_dto_data)
    resolution: Resolution = Resolution.model_validate(resolution_data)

    if callback_data.label != Back.TO_EPISODES:
        fsm_data["translation_label"] = callback_data.label
    videotheatre_dto = VideoTheatreDTO.from_fsm_data(fsm_data, resolution)

    # Handle back button
    selected_season = videotheatre_dto.selected_season
    if callback_data.label == Back.TO_EPISODES:
        await state.update_data(
            {"episode_label": "", "translation_label": ""},
        )
        if len(selected_season.episodes) > 1:
            video_dto.quality = videotheatre_dto.quality
            video_dto.season = selected_season.title
            caption, reply_markup = choose_episodes(
                title_html=video_dto.title_html,
                episodes_list=selected_season.episodes,
            )
        else:
            caption, reply_markup = choose_quality(
                title_html=video_dto.title_html,
                lables=video_dto.unique_labels,
                contenttype=ContentTypeEnum.FILM_DICT,
            )

        await edit_telegram_message_keyboard(query, caption, reply_markup)
    else:
        video_dto.quality = videotheatre_dto.quality_real
        quality_label = videotheatre_dto.quality

        if not quality_label:
            await query.answer(_("selection expired"), show_alert=True)
            return

        # Store translation selection
        await state.update_data(
            {
                "translation_label": callback_data.label,
            },
        )

        # Single translation
        await _trigger_download(
            query=query,
            resolution=resolution,
            format_id=None,
        )

    await query.answer()


@download_router.callback_query(YmdanticFlacCallback.filter())
async def on_flac_select(
    query: CallbackQuery,
    callback_data: YmdanticFlacCallback,
    state: FSMContext,
) -> None:
    """
    Handle user request to download FLAC (lossless) version of a track.

    The actual download logic in YmdanticController will detect this flag
    and proceed with FLAC download instead of standard quality.

    :param query: Callback query object containing button click data
    :param callback_data: Callback data with track label/ID
    :param state: FSM context for storing user session data
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    fsm_data = await state.get_data()
    resolution_data = fsm_data.get("resolution")
    flac_info = fsm_data.get("flac_info", {})

    if not resolution_data:
        await query.message.edit_text(_("selection expired"), reply_markup=None)
        await state.clear()
        return

    if not flac_info or not isinstance(flac_info, dict):
        await query.message.edit_text(_("selection expired"), reply_markup=None)
        await state.clear()
        return

    resolution: Resolution = Resolution.model_validate(resolution_data)
    with contextlib.suppress(TelegramBadRequest):
        await query.message.edit_reply_markup(
            reply_markup=None,
        )

    updated_flac_info = {**flac_info, "download": True}
    await state.update_data(flac_info=updated_flac_info)

    await save_video.kiq(
        resolution=resolution,
        telegram_id=query.from_user.id,
        format_id=None,
    )


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
            SourceEnum.REDDIT_YDL,
        ],
    ),
)
async def download_video(
    message: Message,
    resolution: Resolution,
    state: FSMContext,
    bot: Bot,
) -> None:
    """
    Download video from TikTok, Instagram, Instagram API.

    :param message: Message.
    :param resolution: Resolution.
    """
    if message.from_user is None:
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

    await save_video.kiq(resolution=resolution, telegram_id=message.from_user.id)
