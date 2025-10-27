class DownloaderError(Exception):
    """Base exception for downloader."""


class VideoInfoNotSetError(DownloaderError):
    """Exception for when video info is not set."""

    def __init__(self) -> None:
        super().__init__("Video info not set. Call get_video_info first.")


class YtDlpDownloaderError(DownloaderError):
    """Base exception for yt-dlp."""


class IPAddressBlockedError(DownloaderError):
    """Exception for ip address blocked."""


class TikTokYtDlpDownloaderError(YtDlpDownloaderError):
    """Exception for TikTok yt-dlp."""
