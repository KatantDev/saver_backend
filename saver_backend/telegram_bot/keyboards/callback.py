from aiogram.filters.callback_data import CallbackData

CHECK_SUBSCRIPTIONS = "check_subscriptions"


class VideoFormatCallback(CallbackData, prefix="vid"):
    """Callback for selecting video resolution."""

    label: str


class VideoLanguageCallback(CallbackData, prefix="lang"):
    """Callback for selecting video language from a resolution."""

    format_id: str
