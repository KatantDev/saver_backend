from typing import Any, Type

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from saver_backend.entities.enums import ContentTypeEnum
from saver_backend.entities.enums import KeyboardBacksEnum as Back
from saver_backend.services.downloaders.schema import EpisodeDTO, FormatDTO, SeasonDTO
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.telegram_bot.keyboards.callback import (
    CHECK_SUBSCRIPTIONS,
    VideoEpisodesCallback,
    VideoFormatCallback,
    VideoLanguageCallback,
    VideoSeasonCallback,
    VideoTranslationCallback,
    YmdanticFlacCallback,
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


def get_video_formats_keyboard(
    labels: list[str],
    contenttype: ContentTypeEnum = ContentTypeEnum.VIDEO,
) -> InlineKeyboardMarkup:
    """
    Create a keyboard with buttons for each unique video resolution.

    :param labels: unique video resolution labels.
    :param contenttype: content type of data,
    :return: An InlineKeyboardMarkup.
    """
    builder = InlineKeyboardBuilder()

    for label in labels:
        button_text = f"📹 {label}"

        builder.button(
            text=button_text,
            callback_data=VideoFormatCallback(
                label=label,
                contenttype=contenttype,
            ).pack(),
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
            label=Back.TO_EPISODES,
        ).pack(),
    )

    builder.adjust(1, 1)
    return builder.as_markup()


def _build_grid_keyboard(
    items: list[EpisodeDTO] | list[SeasonDTO],
    row_pattern: list[int],
    callback_class: Type[VideoSeasonCallback] | Type[VideoEpisodesCallback],
    back_label: str,
) -> InlineKeyboardMarkup:
    """
    Build a keyboard with buttons arranged in a grid pattern.

    Buttons with text longer than 20 characters occupy their own row.
    """
    builder = InlineKeyboardBuilder()
    # telegram API supports max 95 buttons
    items = items[:95]
    # Add all item buttons
    text_lengths = []
    for item in items:
        text_lengths.append(len(item.title or ""))
        builder.button(
            text=item.title or "",
            callback_data=callback_class(
                label=item.label,
            ).pack(),
        )

    # Calculate rows configuration
    rows_config = []
    pattern_index = 0
    i = 0

    while i < len(items):
        if text_lengths[i] > 20:
            # Long text button - single button row
            rows_config.append(1)
            i += 1
            pattern_index += 1
        else:
            # Use pattern for normal buttons
            buttons_in_row = row_pattern[pattern_index % len(row_pattern)]

            # Count consecutive normal buttons (not long)
            normal_buttons = 0
            for j in range(i, min(i + buttons_in_row, len(items))):
                if text_lengths[j] > 20:
                    break
                normal_buttons += 1

            if normal_buttons > 0:
                rows_config.append(normal_buttons)
                i += normal_buttons
                pattern_index += 1

    # Add back button
    builder.button(text=_("Back"), callback_data=callback_class(label=back_label))

    # Apply configuration: item rows + 1 row for back button
    builder.adjust(*rows_config, 1)

    return builder.as_markup()


def get_season_keyboard(
    seasons: list[SeasonDTO],
) -> InlineKeyboardMarkup:
    """
    Create a keyboard for selecting a season.

    :param seasons: A list of seasons.
    :return: An InlineKeyboardMarkup.
    """
    return _build_grid_keyboard(
        items=seasons,
        row_pattern=[5, 4],
        callback_class=VideoSeasonCallback,
        back_label=Back.TO_FORMATS,
    )


def get_episodes_keyboard(
    episodes_list: list[EpisodeDTO],
) -> InlineKeyboardMarkup:
    """
    Create a keyboard for selecting episodes within a season.

    :param episodes_list: A list of episodes.
    :return: An InlineKeyboardMarkup.
    """

    return _build_grid_keyboard(
        items=episodes_list,
        row_pattern=[4, 3],
        callback_class=VideoEpisodesCallback,
        back_label=Back.TO_SEASONS,
    )


async def edit_telegram_message_keyboard(
    query: CallbackQuery,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    """
    Edit telegram message keyboard.

    :param query: The callback query object.
    :param caption: New caption or text for the message.
    :param reply_markup: New inline keyboard markup to attach to the message.
    """
    if not isinstance(query.message, Message) or not query.from_user:
        await query.answer()
        return

    if query.message.caption:
        await query.message.edit_caption(caption=caption, reply_markup=reply_markup)
    else:
        await query.message.edit_text(caption, reply_markup=reply_markup)


def get_hq_keyboard(item_id: str) -> InlineKeyboardMarkup:
    """
    Create an inline keyboard with a single button for HQ download.

    The callback data contains the track ID for identification.

    :param item_id: Yandex.Music track identifier used as callback label
    :return: Inline keyboard markup with a single FLAC download button
    """
    builder = InlineKeyboardBuilder()

    button_text = "High Quality [Lossless]"

    builder.button(
        text=button_text,
        callback_data=YmdanticFlacCallback(
            label=item_id,
        ).pack(),
    )
    builder.adjust(1)

    return builder.as_markup()
