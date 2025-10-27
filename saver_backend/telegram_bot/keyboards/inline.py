from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from saver_backend.services.downloaders.schema import FormatDTO, VideoDTO
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.telegram_bot.keyboards.callback import (
    CHECK_SUBSCRIPTIONS,
    VideoFormatCallback,
    VideoLanguageCallback,
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
    video_dto: VideoDTO,
) -> InlineKeyboardMarkup:
    """
    Create a keyboard with buttons for each unique video resolution.

    :param video_dto: The VideoDTO containing format information.
    :return: An InlineKeyboardMarkup.
    """
    builder = InlineKeyboardBuilder()
    sorted_items = sorted(
        video_dto.unique_formats_by_label.items(),
        key=lambda item: item[1][0].height,
        reverse=True,
    )

    for label, _formats in sorted_items:
        button_text = video_dto.get_format_button_text(label)

        builder.button(
            text=button_text,
            callback_data=VideoFormatCallback(label=label).pack(),
        )
    builder.adjust(2)
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
