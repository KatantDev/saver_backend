class DownloaderError(Exception):
    """Base exception for downloader."""


class VideoInfoNotSetError(DownloaderError):
    """Exception for when video info is not set."""

    def __init__(self) -> None:
        super().__init__("Video info not set. Call get_video_info first.")


class UserInfoNotFoundError(DownloaderError):
    """Exception for when user info is not found."""


class YtDlpDownloaderError(DownloaderError):
    """Base exception for yt-dlp."""


class IPAddressBlockedError(DownloaderError):
    """Exception for ip address blocked."""


class TikTokYtDlpDownloaderError(YtDlpDownloaderError):
    """Exception for TikTok yt-dlp."""


class KinovodCaptchaError(DownloaderError):
    """Exception for Kinovod Captcha error."""


class KinovodAlertError(DownloaderError):
    """Exception for Kinovod Alert error."""


class Kinovod404Error(DownloaderError):
    """Exception for Kinovod 404 error."""

class KinovodParseError(Exception):
    """Exception for Kinovod Parse error."""

class KinovodQualityParseError(KinovodParseError):
    """Exception for Kinovod QualityParse error."""

class KinovodTranslationParseError(KinovodParseError):
    """Exception for Kinovod translation parse error."""
