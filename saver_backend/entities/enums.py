from enum import Enum


class SourceEnum(str, Enum):
    """Enum for source to download."""

    TIKTOK = "tiktok"
    INSTAGRAM_YDL = "instagram_ydl"
    INSTAGRAM_API = "instagram_api"
    YOUTUBE_SHORTS_YDL = "youtube_shorts_ydl"
    YOUTUBE_VIDEO_YDL = "youtube_video_ydl"
    VK_CLIPS_YDL = "vk_clips_ydl"
    VK_VIDEO_YDL = "vk_video_ydl"
    PINTEREST_YDL = "pinterest_ydl"
    UNSUPPORTED = "unsupported"


class InstagramContentTypeEnum(str, Enum):
    """Enum for content type of Instagram."""

    POST = "post"
    IGTV = "igtv"
    STORIES = "stories"
    REELS = "reels"
