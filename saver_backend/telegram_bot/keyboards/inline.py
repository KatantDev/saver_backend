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
    :param msg_id: The ID of the message this keyboard will be attached to.
    :return: An InlineKeyboardMarkup.
    """
    builder = InlineKeyboardBuilder()
    sorted_labels = sorted(
        video_dto.unique_formats_by_label.keys(),
        key=lambda label: int(label.replace("p", "")),
        reverse=True,
    )

    for label in sorted_labels:
        formats = video_dto.unique_formats_by_label[label]
        if not formats:
            continue
        example_format = formats[0]
        button_text = label
        if size := example_format.formatted_filesize:
            button_text += f" (~{size})"

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
    :param msg_id: The ID of the message this keyboard will be attached to.
    :return: An InlineKeyboardMarkup.
    """
    builder = InlineKeyboardBuilder()
    for fmt in formats:
        lang = fmt.language or "Default"
        button_text = f"{lang.capitalize()}"
        if size := fmt.formatted_filesize:
            button_text += f" (~{size})"
        builder.button(
            text=button_text,
            callback_data=VideoLanguageCallback(
                format_id=fmt.format_id,
            ).pack(),
        )
    builder.adjust(2)
    return builder.as_markup()
