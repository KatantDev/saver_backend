import re
from typing import Any

import aiogram.types
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from saver_backend.entities.enums import ContentTypeEnum
from saver_backend.services.i18n import gettext as _
from saver_backend.telegram_bot.keyboards.inline import (
    get_episodes_keyboard,
    get_language_keyboard,
    get_season_keyboard,
    get_translations_keyboard,
    get_video_formats_keyboard,
)


async def check_fsm_data(
    query: CallbackQuery,
    fsm_data: dict[str, Any],
    message: str,
) -> bool:
    """
    Check if required FSM data is present.

    :param query: The callback query object.
    :param fsm_data: Dictionary containing FSM state data.
    :param message: Error message key to display when data is missing.
    :return: True if both video_dto and resolution are present, False otherwise.
    """
    if not isinstance(query.message, aiogram.types.Message) or not query.from_user:
        await query.answer()
        return False

    video_dto_data = fsm_data.get("video_dto")
    resolution_data = fsm_data.get("resolution")

    if not video_dto_data or not resolution_data:
        await query.message.edit_text(_(message), reply_markup=None)
        return False
    return True


def choose_season(
    title_html: str,
    seasons: list[dict[str, Any]],
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Generate caption and keyboard for season selection.

    :param title_html: video title HTML string
    :param seasons: List of available seasons
    :return: Tuple of (caption text, inline keyboard markup)
    """
    caption = _("choose season").format(title=title_html)
    season_titles = [item["title"] for item in seasons]
    reply_markup = get_season_keyboard(season_titles)
    return caption, reply_markup


def choose_language(
    title_html: str,
    label: str,
    formats: list[Any],
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Generate caption and keyboard for language selection.

    :param title_html: Video title HTML str
    :param label: Quality/format label
    :param formats: List of available formats with languages
    :return: Tuple of (caption text, inline keyboard markup)
    """
    caption = _("choose language for download").format(
        title=title_html,
        label=label,
    )
    reply_markup = get_language_keyboard(formats)
    return caption, reply_markup


def choose_quality(
    title_html: str,
    lables: list[str],
    contenttype: ContentTypeEnum = ContentTypeEnum.VIDEO,
    lang: str = "en",
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Generate caption and keyboard for quality selection.

    :param title_html: Video title HTML str
    :param lables: List of available quality labels
    :param contenttype: ContentType enum
    :param lang: Language code for localization
    :return: Tuple of (caption text, inline keyboard markup)
    """
    caption = _("choose quality", locale=lang).format(title=title_html)
    reply_markup = get_video_formats_keyboard(
        labels=lables,
        contenttype=contenttype,
    )
    return caption, reply_markup


def choose_translations(
    title_html: str,
    season_label: str,
    translations: dict[str, Any],
    lang: str = "en",
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Generate caption and keyboard for translation selection.

    :param title_html: Video title HTML str
    :param season_label: Season number/label
    :param translations: Dictionary of available translations
    :param lang: Language code for localization
    :return: Tuple of (caption text, inline keyboard markup)
    """
    caption = _("choose translations", locale=lang).format(
        title=title_html,
        season=season_label,
    )
    reply_markup = get_translations_keyboard(translations=translations)
    return caption, reply_markup


def choose_series(
    title_html: str,
    season_label: str,
    translation_name: str,
    episodes_list: list[dict[str, Any]],
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Generate caption and keyboard for series/episode selection.

    :param title_html: Video title HTML str
    :param season_label: Season number/label
    :param translation_name: Name of the translation/dubbing
    :param episodes_list: List of available series/episodes
    :return: Tuple of (caption text with normalized line breaks, inline keyboard markup)
    """
    caption = _("choose series").format(
        title=title_html,
        season=f"› {season_label}" if season_label else "",  # noqa: RUF001
        translation=f"› {translation_name}" if translation_name else "",  # noqa: RUF001
    )
    caption = re.sub(r"\n{3,}", "\n\n", caption)
    reply_markup = get_episodes_keyboard(episodes_list=episodes_list)
    return caption, reply_markup
