class DownloaderError(Exception):
    """Base exception for downloader."""


class YtDlpDownloaderError(DownloaderError):
    """Base exception for yt-dlp."""


class TikTokYtDlpDownloaderError(YtDlpDownloaderError):
    """Exception for TikTok yt-dlp."""
