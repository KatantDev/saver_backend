import asyncio
import logging
from typing import Any, ClassVar

from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController


class YouTubeVideoYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from YouTube through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.YOUTUBE_VIDEO_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for YouTube."""
        super().__init__(*args, **kwargs)

        youtube_params = {
            "downloader": "aria2c",
            "external_downloader_args": {
                "aria2c": [
                    "-x",
                    "16",
                    "-s",
                    "16",
                    "-k",
                    "1M",
                    "--timeout=60",  # Таймаут для соединения
                    "--max-tries=10",  # Максимальное количество попыток
                    "--retry-wait=5",  # Ждать между попытками
                ],
            },
            "extractor_args": {
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://saver_backend-bgutil:4416"],
                },
            },
            "remote_components": ["ejs:github"],
        }
        self._yt_dlp.params.update(youtube_params)

    def _remove_combined_formats(
        self, formats: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        combined_formats = [
            fmt
            for fmt in formats
            if fmt.get("acodec") != "none" and fmt.get("vcodec") != "none"
        ]

        if len(combined_formats) > 1:
            return None

        if len(combined_formats) == 1:
            return [fmt for fmt in formats if fmt not in combined_formats]

        return formats

    def _edit_format_info_dict(self, info_dict: dict[str, Any]) -> dict[str, Any]:
        formats = info_dict.get("formats", [])
        if not formats:
            return info_dict

        formats = self._remove_combined_formats(formats)
        if formats is None:
            return info_dict

        best_audio = None
        best_audio_priority = 0

        for fmt in formats:
            if (
                fmt.get("acodec") != "none"
                and fmt.get("vcodec") == "none"
                and fmt.get("video_ext") == "none"
                and fmt.get("audio_ext") != "none"
            ):
                # Priority: higher bitrate is better, if available
                abr = fmt.get("abr") or 0
                if abr > best_audio_priority:
                    best_audio_priority = abr
                    best_audio = fmt

        if not best_audio:
            logging.warning("[youtup] Video dont have audio")
            return info_dict

        best_audio_id = best_audio.get("format_id")
        best_audio_codec = best_audio.get("acodec")

        # Modify formats: combine mp4 video-only with best audio
        modified_formats = []
        for fmt in formats:
            if (
                fmt.get("video_ext") == "mp4"
                and fmt.get("acodec") == "none"
                and fmt.get("vcodec") != "none"
            ):
                modified_fmt = fmt.copy()
                original_format_id = fmt.get("format_id", "")
                modified_fmt["format_id"] = f"{original_format_id}+{best_audio_id}"
                modified_fmt["acodec"] = best_audio_codec
                modified_formats.append(modified_fmt)
            else:
                modified_formats.append(fmt)

        info_dict["formats"] = modified_formats

        return info_dict

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

            info_dict = self._edit_format_info_dict(info_dict)

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

    async def download_video(self) -> None:
        """
        Download a video, checking the cache first.

        If a cached version (file_id) exists, it sends it directly.
        Otherwise, it proceeds with the full download process.
        """

        if self._selected_format_id and "+" in self._selected_format_id:
            self._yt_dlp.params.update(
                {
                    "format": self._selected_format_id,
                    "merge_output_format": "mp4",
                }
            )
            self._yt_dlp = self._create_yt_dlp(self._yt_dlp.params)

        await super().download_video()
