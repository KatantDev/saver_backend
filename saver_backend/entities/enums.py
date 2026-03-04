from enum import Enum


class ProxyType(str, Enum):
    """Enum for proxy type to use for a source."""

    LOCAL = "local"
    RU = "ru"
    ALL = "all"


class ContentTypeEnum(str, Enum):
    """Enum for content type of cached item."""

    VIDEO = "video"
    PHOTO = "photo"
    AUDIO = "audio"
    PHOTO_LIST = "photo_list"


class SourceEnum(str, Enum):
    """Enum for source to download."""

    TIKTOK = "tiktok"
    INSTAGRAM_YDL = "instagram_ydl"
    INSTAGRAM_API = "instagram_api"
    INSTAGRAM_INDOWN = "instagram_indown"
    INSTAGRAM_INSTALOADER = "instagram_instaloader"
    YOUTUBE_SHORTS_YDL = "youtube_shorts_ydl"
    YOUTUBE_VIDEO_YDL = "youtube_video_ydl"
    VK_CLIPS_YDL = "vk_clips_ydl"
    VK_VIDEO_YDL = "vk_video_ydl"
    OK_YDL = "ok_ydl"
    PINTEREST_YDL = "pinterest_ydl"
    RUTUBE_YDL = "rutube_ydl"
    X_YDL = "x_ydl"
    DZEN_YDL = "dzen_ydl"
    ADULT_YDL = "adult_ydl"
    FACEBOOK_YDL = "facebook_ydl"
    UNSUPPORTED = "unsupported"
    M3U8_YDL = "m3u8_ydl"


class InstagramContentTypeEnum(str, Enum):
    """Enum for content type of Instagram."""

    POST = "post"
    IGTV = "igtv"
    STORIES = "stories"
    REELS = "reels"
