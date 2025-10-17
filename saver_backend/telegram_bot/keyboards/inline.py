from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.telegram_bot.keyboards.callback import (
    CHECK_SUBSCRIPTIONS,
    VK_QUALITY_PREFIX,
    VK_QUALITY_720P,
    VK_QUALITY_480P,
    VK_QUALITY_360P,
    VK_QUALITY_240P,
    VK_QUALITY_BEST,
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


def get_vk_quality_keyboard(available_formats: list) -> InlineKeyboardMarkup:
    """
    Получение 
    """
    builder = InlineKeyboardBuilder()
    quality_mapping = {
        "720p": VK_QUALITY_720P,
        "480p": VK_QUALITY_480P,
        "360p": VK_QUALITY_360P,
        "240p": VK_QUALITY_240P,
        "best": VK_QUALITY_BEST,
    }
    for fmt in available_formats:
        quality = fmt.get('quality', 'unknown')
        if quality in quality_mapping:
            builder.button(
                text=f"📹 {quality}",
                callback_data=quality_mapping[quality]
            )
    if VK_QUALITY_BEST not in [quality_mapping.get(fmt.get('quality', ''), '') for fmt in available_formats]:
        builder.button(
            text="🎯 Лучшее качество",
            callback_data=VK_QUALITY_BEST
        )
    builder.adjust(2)
    return builder.as_markup()
