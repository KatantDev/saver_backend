from aiogram.filters.callback_data import CallbackData

from saver_backend.entities.enums import ContentTypeEnum

CHECK_SUBSCRIPTIONS = "check_subscriptions"


class VideoFormatCallback(CallbackData, prefix="vid"):
    """Callback for selecting video resolution."""

    label: str
    contenttype: ContentTypeEnum


class VideoLanguageCallback(CallbackData, prefix="lang"):
    """Callback for selecting video language from a resolution."""

    format_id: str


class VideoSeasonCallback(CallbackData, prefix="seas"):
    """Callback for selecting video season."""

    label: str


class VideoTranslationCallback(CallbackData, prefix="transl"):
    """Callback for selecting translations."""

    label: str


class VideoEpisodesCallback(CallbackData, prefix="series"):
    """Callback for selecting series within a season."""

    label: str
