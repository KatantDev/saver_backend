from enum import Enum


class SourceEnum(str, Enum):
    """Enum for source to download."""

    TIKTOK = "tiktok"
    INSTAGRAM_YDL = "instagram_ydl"
    INSTAGRAM_API = "instagram_api"
    UNSUPPORTED = "unsupported"
    UNKNOWN_URL = "unknown_url"


class InstagramContentTypeEnum(str, Enum):
    """Enum for content type of Instagram."""

    POST = "post"
    IGTV = "igtv"
    STORIES = "stories"
    REELS = "reels"
