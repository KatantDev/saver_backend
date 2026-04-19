from typing import Any

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from saver_backend.services.downloaders.schema import FormatDTO
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.telegram_bot.keyboards.callback import (
    CHECK_SUBSCRIPTIONS,
    VideoFormatCallback,
    VideoLanguageCallback,
    VideoSeasonCallback,
    VideoSeriesCallback,
    VideoTranslationCallback,
)


def get_start_keyboard(username: str) -> InlineKeyboardMarkup:
    """
    Get start keyboard.

    :param username: username of the user.
    :return: start keyboard.
    """

    builder = InlineKeyboardBuilder()

    # Invite Button
    text = "Send popular memes as video messages"
    url = f"https://t.me/{username}"
    builder.button(
        text="Share with friends",
        url=f"https://t.me/share/url?url={url}&text={text}",
    )
    # Join Community Button
    builder.button(
        text="Not Meme",
        url="https://t.me/notmeme",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_subscribe_keyboard() -> InlineKeyboardMarkup:
    """
    Get subscribe keyboard.

    :return: subscribe keyboard.
    """
    builder = InlineKeyboardBuilder()
    for channel in settings.subscription_channels:
        builder.button(
            text=_("subscribe to channel").format(channel=channel),
            url=f"https://t.me/{channel}",
        )
    builder.button(text=_("check subscriptions"), callback_data=CHECK_SUBSCRIPTIONS)
    builder.adjust(1)
    return builder.as_markup()


def get_video_formats_keyboard(labels: list[str]) -> InlineKeyboardMarkup:
    """
    Create a keyboard with buttons for each unique video resolution.

    :param labels: unique video resolution labels.
    :return: An InlineKeyboardMarkup.
    """
    builder = InlineKeyboardBuilder()
    for label in labels:
        button_text = f"📹 {label}"

        builder.button(
            text=button_text,
            callback_data=VideoFormatCallback(label=label).pack(),
        )
    builder.adjust(1)
    return builder.as_markup()


def get_language_keyboard(
    formats: list[FormatDTO],
) -> InlineKeyboardMarkup:
    """
    Create a keyboard for selecting a language for a specific resolution.

    :param formats: A list of FormatDTOs for the same resolution.
    :return: An InlineKeyboardMarkup.
    """
    builder = InlineKeyboardBuilder()
    for fmt in formats:
        builder.button(
            text=fmt.language_button_text,
            callback_data=VideoLanguageCallback(
                format_id=fmt.format_id,
            ).pack(),
        )
    builder.adjust(2)
    return builder.as_markup()


def get_season_keyboard(
    seasons: list[str],
) -> InlineKeyboardMarkup:
    """
    Create a keyboard for selecting a language for a specific resolution.

    :param seasons: A list of seasons.
    :return: An InlineKeyboardMarkup.
    """
    builder = InlineKeyboardBuilder()
    for season in seasons:
        builder.button(
            text=season,
            callback_data=VideoSeasonCallback(
                label=season,
            ).pack(),
        )

    # Calculate rows: alternate between 5 and 4 buttons
    buttons_count = len(seasons)
    rows_config = []
    row_index = 0
    remaining = buttons_count

    while remaining > 0:
        # Even row index (0, 2, 4...) -> 4 buttons
        # Odd row index (1, 3, 5...) -> 3 buttons
        buttons_in_row = 5 if row_index % 2 == 0 else 4
        if remaining >= buttons_in_row:
            rows_config.append(buttons_in_row)
            remaining -= buttons_in_row
        else:
            rows_config.append(remaining)
            remaining = 0
        row_index += 1

    builder.button(
        text=_("Back"),
        callback_data=VideoSeasonCallback(
            label="back_to_formats",
        ).pack(),
    )
    # Apply configuration: series rows + 1 row for back button
    builder.adjust(*rows_config, 1)
    return builder.as_markup()


def get_translations_keyboard(
    translations: dict[str, Any],
) -> InlineKeyboardMarkup:
    """
    Create a keyboard for selecting a translations for a specific resolution.

    :param translations: A list of translations.
    :return: An InlineKeyboardMarkup.
    """
    builder = InlineKeyboardBuilder()
    for key, translation in translations.items():
        builder.button(
            text=translation,
            callback_data=VideoTranslationCallback(
                label=key,
            ).pack(),
        )
    builder.adjust(1)
    builder.button(
        text=_("Back"),
        callback_data=VideoTranslationCallback(
            label="back_to_season",
            part2="",
        ).pack(),
    )
    builder.adjust(1, 1)
    return builder.as_markup()


def get_series_keyboard(
    series_list: list[dict[str, Any]],
) -> InlineKeyboardMarkup:
    """
    Create a keyboard for selecting series within a season.

    Displays 4 buttons per row on even rows, 3 buttons per row on odd rows.

    :param series_list: A list of series numbers or names.
    :return: An InlineKeyboardMarkup.
    """
    builder = InlineKeyboardBuilder()

    for episode in series_list:
        builder.button(
            text=f"{episode.get('title')}",
            callback_data=VideoSeriesCallback(
                label=episode.get("title"),
            ).pack(),
        )

    # Calculate rows: alternate between 4 and 3 buttons
    buttons_count = len(series_list)
    rows_config = []
    row_index = 0
    remaining = buttons_count

    while remaining > 0:
        # Even row index (0, 2, 4...) -> 4 buttons
        # Odd row index (1, 3, 5...) -> 3 buttons
        buttons_in_row = 4 if row_index % 2 == 0 else 3
        if remaining >= buttons_in_row:
            rows_config.append(buttons_in_row)
            remaining -= buttons_in_row
        else:
            rows_config.append(remaining)
            remaining = 0
        row_index += 1

    # Add back button
    builder.button(
        text=_("Back"),
        callback_data=VideoSeriesCallback(
            label="back_to_translations",
        ).pack(),
    )

    # Apply configuration: series rows + 1 row for back button
    builder.adjust(*rows_config, 1)

    return builder.as_markup()
