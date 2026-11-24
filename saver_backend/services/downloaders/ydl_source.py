import asyncio
import logging
import re
import secrets
from abc import ABC
from pathlib import Path
from typing import Any, ClassVar, Dict, Sequence

import sentry_sdk
import yt_dlp
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.consts import (
    BASE_DOWNLOAD_PATH,
    TELEGRAM_UPLOAD_LIMIT_BYTES,
)
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.exceptions import (
    FileTooLargeError,
    IPAddressBlockedError,
    VideoInfoNotSetError,
)
from saver_backend.services.downloaders.schema import AudioDTO, PhotoDTO, VideoDTO

DOWNLOAD_RETRIES = 10
RETRY_PAUSE_SECONDS = 2


COOKIE_FILE_HEADER_RE = re.compile(r"#( Netscape)? HTTP Cookie File")


def is_cookie_file(path: Path) -> bool:
    """
    Whether yt-dlp can load the cookie file.

    Prod 23.11: cookies/adult_ydl/cookies1.txt was empty, and yt-dlp refused to
    start («does not look like a Netscape format cookies file»), so every adult
    link failed. Such a file is skipped and the video is read without cookies.

    :param path: Cookie file.
    :return: True when the file starts with the Netscape cookie header.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as file:
            return COOKIE_FILE_HEADER_RE.match(file.readline()) is not None
    except OSError:
        return False


def _retry_backoff(n: int) -> float:
    """
    Pause before a repeated request of yt-dlp, in seconds.

    yt-dlp calls it by keyword, ``sleep_func(n=...)``, so the parameter has to
    be named ``n``: with another name every repeat after a network hiccup
    raised TypeError and the download failed (24 on prod in 5 hours on 23.11,
    mostly YouTube).

    :param n: Number of the repeat, from 0.
    :return: 1, 2, 4, 8 and then 10 seconds.
    """
    return float(min(2**n, 10))


class YtDlpController(BaseSourceController, ABC):
    """Asynchronous controller for downloading videos through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.UNSUPPORTED
    COOKIES: ClassVar[bool] = False
    SUPPORTS_STREAMING: ClassVar[bool] = True
    DIRECT_URL_DOWNLOAD: ClassVar[bool] = False
    # Extra attempts (each through the next proxy) when yt-dlp fails;
    # sources whose failures are known to be transient turn it on
    RETRIES_ON_ERROR: ClassVar[int] = 0
    # One more try without a proxy when the proxied ones failed (task 6)
    DIRECT_FALLBACK: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._video: VideoDTO | None = None

        # Get download directory and create it
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)

        # Per-instance suffix to avoid filename collisions when multiple
        # workers download the same source_id concurrently (e.g. same video
        # in different qualities). Without it, yt-dlp's HLS fragments from
        # parallel downloads can overwrite each other and produce empty files.
        self._download_token = secrets.token_hex(4)
        # Bytes on disk per file of the current download (video and audio
        # streams are separate files), for the size check in the progress hook
        self._bytes_by_file: dict[str, int] = {}

        self._base_options: dict[str, Any] = {
            "proxy": self._proxy,
            "format": "best",
            "outtmpl": str(
                self._download_directory / f"%(id)s.{self._download_token}.%(ext)s",
            ),
            "noplaylist": True,
            "writethumbnail": True,
            "quiet": True,
            "noprogress": True,
            "overwrites": True,
            "postprocessor_args": [
                "-nostdin",
            ],
            "retries": DOWNLOAD_RETRIES,
            "fragment_retries": DOWNLOAD_RETRIES,
            "retry_sleep_functions": {
                "http": _retry_backoff,
                "fragment": _retry_backoff,
            },
        }
        if self._selected_format_id:
            self._base_options["format"] = self._selected_format_id

        # Set cookies if needed and initialize yt-dlp controller
        self._set_cookies()
        self._yt_dlp = self._create_yt_dlp(self._base_options)

    async def close(self) -> None:
        """Close the HTTP client."""
        self._yt_dlp.close()

    def _create_yt_dlp(self, params: dict[str, Any]) -> yt_dlp.YoutubeDL:
        if hasattr(self, "_yt_dlp"):
            self._yt_dlp.close()

        controller = yt_dlp.YoutubeDL(params)
        controller.add_progress_hook(self._progress_hook)
        return controller

    def _set_proxy(self) -> None:
        try:
            self._proxy = self._proxies.pop(0)
        except IndexError as error:
            raise IPAddressBlockedError from error
        self._yt_dlp = self._create_yt_dlp(
            {
                **self._yt_dlp.params,
                "proxy": self._proxy,
            },
        )

    @property
    def video_info(self) -> VideoDTO:
        """
        Get the video information.

        :return: The video information.
        """
        if not self._video:
            raise VideoInfoNotSetError
        return self._video

    def _set_cookies(self, force_set: bool = False) -> None:
        if not self.COOKIES and not force_set:
            return

        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        cookie_dir = base_dir / "cookies" / self.SOURCE.value
        found = list(cookie_dir.glob("cookies*.txt"))
        cookie_files = [path for path in found if is_cookie_file(path)]
        if len(cookie_files) < len(found):
            logging.warning(
                "Skipped %s empty or broken cookie files in %s",
                len(found) - len(cookie_files),
                cookie_dir,
            )
        if not cookie_files:
            logging.warning(
                "Cookies enabled for %s, but no usable files found in %s",
                self.SOURCE,
                cookie_dir,
            )
            return

        cookie_file = secrets.choice(cookie_files).resolve()
        self._base_options["cookiefile"] = str(cookie_file)
        logging.info("Using cookie file %s for %s", cookie_file, self.SOURCE)

    async def download_video(self) -> None:
        """
        Download a video, checking the cache first.

        If a cached version (file_id) exists, it sends it directly.
        Otherwise, it proceeds with the full download process.
        """
        cache_quality_key = self._selected_format_id or "best"

        url_code = str(self._resolution.metadata.get("code"))
        if await self.send_video_from_cache(
            source_id=url_code,
            quality=cache_quality_key,
        ):
            return

        attempts = self.RETRIES_ON_ERROR + 1
        direct_fallback = self.DIRECT_FALLBACK and bool(self._proxy)
        if direct_fallback:
            attempts += 1
        for attempt in range(attempts):
            last_attempt = attempt == attempts - 1
            try:
                await self._fetch_and_send(
                    cache_quality_key, raise_errors=not last_attempt
                )
                return
            except DownloadError as e:
                if last_attempt:
                    raise
                go_direct = direct_fallback and attempt == attempts - 2
                logging.warning(
                    "[%s] attempt %s failed for %s, retrying %s: %s",
                    self.SOURCE.value,
                    attempt + 1,
                    self._resolution.url,
                    "without a proxy" if go_direct else "through another proxy",
                    e,
                )
                if go_direct:
                    self._switch_to_direct()
                else:
                    self._switch_proxy_for_retry()
                await asyncio.sleep(RETRY_PAUSE_SECONDS)

    def _switch_to_direct(self) -> None:
        """Retry from the server's own address once every proxy has failed."""
        self._proxy = ""
        self._proxies = []
        self._yt_dlp = self._create_yt_dlp({**self._yt_dlp.params, "proxy": ""})

    def _switch_proxy_for_retry(self) -> None:
        """Take the next proxy, or a fresh yt-dlp session if none are left."""
        try:
            self._set_proxy()
        except IPAddressBlockedError:
            self._yt_dlp = self._create_yt_dlp(self._yt_dlp.params)

    async def _fetch_and_send(self, cache_quality_key: str, raise_errors: bool) -> None:
        """
        Read the video info, then send it from the cache or download and send it.

        :param cache_quality_key: Quality the cache is keyed by.
        :param raise_errors: Re-raise yt-dlp errors so the caller can retry,
            instead of sending the user an error message.
        """
        # Getting information about the video, if data is invalid, send error message
        try:
            info_dict = await self.get_video_info(url=self._resolution.url)
        except DownloadError as e:
            if raise_errors:
                raise
            logging.exception(e)
            await self._send_error_message()
            return

        if not self._video or not info_dict:
            return

        if not self._video.source_id:
            logging.error("Could not determine source_id.")
            return

        # Get the video from the cache if it was previously downloaded
        if await self.send_video_from_cache(
            source_id=self._video.source_id,
            quality=cache_quality_key,
        ):
            return

        logging.info(
            "Cache miss for source_id=%s and quality=%s. Starting download.",
            self._video.source_id,
            cache_quality_key,
        )

        if self._video.direct_download_url:
            logging.info("Attempting direct URL send for source %s.", self.SOURCE)
            await self._send_video(
                self._video,
                supports_streaming=self.SUPPORTS_STREAMING,
            )
        else:
            logging.info("Starting full download for source %s.", self.SOURCE)
            await self._execute_download(info_dict)

    async def _execute_download(self, info_dict: dict[str, Any]) -> None:
        """
        Executes the actual download and sending logic.

        Subclasses can override this to add specific error handling.

        :param info_dict: The dictionary from yt_dlp.extract_info.
        """
        self._process_percent(percent=16)
        self._bytes_by_file = {}

        try:
            await asyncio.to_thread(self._yt_dlp.process_info, info_dict)
        except FileTooLargeError as e:
            logging.error(
                "The file of %s is over Telegram's limit, download stopped: %s",
                self._video.source_id if self._video else self._resolution.url,
                e,
            )
            self._remove_partial_files()
            await self._telegram_bot_controller.send_file_too_large(
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )
            self._message_id = None
            return
        except DownloadError as e:
            error_message = str(e)
            if "HTTP Error 403: Forbidden" in error_message:
                logging.error(
                    f"[{self.SOURCE.value}]"
                    f"  download failed due to expired cookies (403)."
                )
            raise e
        except Exception as e:
            # Logged everywhere: on prod 150 YouTube failures in two days had no
            # error text in failed_request because this branch only told Sentry.
            logging.exception(
                "[%s] download failed for %s: %s",
                self.SOURCE.value,
                self._resolution.url,
                e,
            )
            self._remove_partial_files()
            await self._send_error_message()
            sentry_sdk.capture_exception(e)
            return

        if not self._video or not self._video.path:
            await self._send_error_message()
            logging.error("Could not determine final filepath from yt-dlp result.")
            return

        if not Path(self._video.path).exists():
            await self._send_error_message()
            logging.error(
                "yt-dlp reported success, but file %s does not exist.",
                self._video.path,
            )
            return

        thumbnail = self._get_thumbnail(source_id=self._video.source_id)
        self._video = self._video.model_copy(update={"thumbnail": thumbnail})

        await self._send_video(
            video_dto=self._video,
            supports_streaming=self.SUPPORTS_STREAMING,
        )
        self.cleanup_files([self._video])

    def _remove_partial_files(self) -> None:
        """
        Delete what a stopped download left on disk.

        yt-dlp keeps ``.part``, fragment and ``.ytdl`` files to resume later; this
        download will not be resumed, and such files filled the disk (prod
        24.11: 22 GB of abandoned VK files).
        """
        for path in self._download_directory.glob(f"*.{self._download_token}.*"):
            try:
                path.unlink()
            except OSError as e:
                logging.warning("Could not delete %s: %s", path, e)

    def _check_size(self, d: Dict[str, Any]) -> None:
        """
        Stop the download once the file is known to be over Telegram's limit.

        The size of some files is unknown until the download starts (VK's plain
        https files, streams). The bot used to download them whole and only then
        answer «file too large»: a 21-hour VK video, 3.9 GB, took 4.5 hours of
        downloading on prod on 23.11. The exact size comes with the first
        progress report of a plain file; for a stream the bytes already
        downloaded are counted (its estimate is too rough to trust).

        :param d: Progress report of yt-dlp.
        :raises FileTooLargeError: The file cannot be sent to Telegram.
        """
        name = str(d.get("filename") or "")
        known = int(d.get("total_bytes") or 0)
        done = int(d.get("downloaded_bytes") or 0)
        self._bytes_by_file[name] = max(known, done)
        size = sum(self._bytes_by_file.values())
        if size > TELEGRAM_UPLOAD_LIMIT_BYTES:
            raise FileTooLargeError(size)

    def _progress_hook(self, d: Dict[str, Any]) -> None:
        """Report download progress and stop a download that is too large."""
        if d["status"] == "downloading":
            self._check_size(d)
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded_bytes = d.get("downloaded_bytes")
            if not total_bytes or not downloaded_bytes or total_bytes <= 0:
                return

            percent_float = (downloaded_bytes / total_bytes) * 100
            percent = round(percent_float * 0.66 + 16)

            if (
                self._last_percent + 10 >= percent >= self._last_percent - 10
                and self._message_id is not None
            ):
                return

            self._process_percent(percent=percent)

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video information without downloading.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        """
        try:
            info_dict = await asyncio.to_thread(
                self._yt_dlp.extract_info,
                url=url,
                download=False,
            )

            video_id = info_dict.get("id")
            video_ext = info_dict.get("ext")

            predicted_path = (
                self._download_directory
                / f"{video_id}.{self._download_token}.{video_ext}"
            )

            self._video = VideoDTO.from_yt_dlp(
                info=info_dict,
                file_path=predicted_path,
                extract_direct_links=self.DIRECT_URL_DOWNLOAD,
                quality=self._selected_format_id or "best",
            )

            return info_dict
        except DownloadError as e:
            if (
                "Your IP address is blocked from accessing this post" in e.msg
                or "Unable to connect to proxy" in e.msg
                or "SOCKS server failure" in e.msg
            ):
                self._set_proxy()
                return await self.get_video_info(url=url)
            if "Unsupported URL" in str(e) or "HTTP Error 404" in str(e):
                await self.delete_processing_message()
                await self._telegram_bot_controller.send_content_not_found_error(
                    telegram_id=self._telegram_id,
                )
                return None
            raise

    def cleanup_files(self, dtos: Sequence[VideoDTO | PhotoDTO | AudioDTO]) -> None:
        """
        Safely deletes the downloaded files and thumbnails from a list of DTOs.

        :param dtos: List of DTOs (VideoDTO, PhotoDTO).
        """
        for item in dtos:
            if item.path:
                file_path = Path(item.path)
                if file_path.exists():
                    file_path.unlink()

            if isinstance(item, VideoDTO) and item.thumbnail:
                thumb_path = Path(item.thumbnail)
                if thumb_path.exists():
                    thumb_path.unlink()

    def _get_thumbnail(self, source_id: str | None) -> Path | None:
        if not source_id:
            return None

        possible_extensions = (".webp", ".png", ".jpg", ".jpeg", ".image")
        for ext in possible_extensions:
            thumb_path = (
                self._download_directory / f"{source_id}.{self._download_token}{ext}"
            )
            if not thumb_path.exists():
                continue
            return thumb_path
        return None
