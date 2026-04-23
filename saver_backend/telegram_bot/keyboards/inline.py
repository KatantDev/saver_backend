from typing import Any, Type

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from saver_backend.entities.enums import ContentTypeEnum
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
            label="back_to_season",
        ).pack(),
    )

    builder.adjust(1, 1)
    return builder.as_markup()


def _build_grid_keyboard(
    items: list[str],
    row_pattern: list[int],
    callback_class: Type[VideoSeasonCallback] | Type[VideoSeriesCallback],
    back_label: str,
) -> InlineKeyboardMarkup:
    """
    Build a keyboard with buttons arranged in a grid pattern.

    :param items: List of button texts.
    :param row_pattern: Pattern of buttons per row (e.g., [5, 4]  ).
    :param callback_class: Callback data for the back button.
    :param back_label: Text for the back button. If None, uses localized "Back".
    :return: An InlineKeyboardMarkup.
    """
    builder = InlineKeyboardBuilder()

    # Add all item buttons
    for item in items:
        builder.button(
            text=item,
            callback_data=callback_class(
                label=item,
            ).pack(),
        )

    # Calculate rows configuration based on pattern
    buttons_count = len(items)
    rows_config = []
    pattern_index = 0
    remaining = buttons_count

    while remaining > 0:
        buttons_in_row = row_pattern[pattern_index % len(row_pattern)]
        if remaining >= buttons_in_row:
            rows_config.append(buttons_in_row)
            remaining -= buttons_in_row
        else:
            rows_config.append(remaining)
            remaining = 0
        pattern_index += 1

    # Add back button
    builder.button(text=_("Back"), callback_data=callback_class(label=back_label))

    # Apply configuration: item rows + 1 row for back button
    builder.adjust(*rows_config, 1)

    return builder.as_markup()


def get_season_keyboard(
    seasons: list[str],
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
        back_label="back_to_formats",
    )


def get_episodes_keyboard(
    episodes_list: list[dict[str, Any]],
) -> InlineKeyboardMarkup:
    """
    Create a keyboard for selecting episodes within a season.

    :param episodes_list: A list of episodes.
    :return: An InlineKeyboardMarkup.
    """
    episode_titles: list[str] = [episode.get("title", "") for episode in episodes_list]

    return _build_grid_keyboard(
        items=episode_titles,
        row_pattern=[4, 3],
        callback_class=VideoSeriesCallback,
        back_label="back_to_translations",
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
