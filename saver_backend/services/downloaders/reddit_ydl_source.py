import asyncio
from typing import Any, ClassVar
from urllib.parse import unquote

from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.schema import PhotoDTO, VideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController


class RedditYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Reddit through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.REDDIT_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for YouTube."""
        super().__init__(*args, **kwargs)

        reddit_params = {
            "format": "bestaudio+bestvideo",
            "downloader": "aria2c",
            "external_downloader_args": {
                "aria2c": [
                    "-x",
                    "16",
                    "-s",
                    "16",
                    "-k",
                    "1M",
                ],
            },
        }
        self._yt_dlp.params.update(reddit_params)
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=reddit_params["format"],
        )

    async def _trigger_download_gif(self, url: str) -> None:
        """
        Creates PhotoDto from url and send animation to chat.

        :param url: URL.
        :return: .
        """
        source_id = self._resolution.metadata.get("code", "")
        photo_dto = PhotoDTO.from_url(
            image_url=url,
            source_id=source_id,
            resolution_url=self._resolution.url,
        )
        await self._telegram_bot_controller.send_finish_downloading_gif(
            animation=photo_dto,
            telegram_id=self._telegram_id,
            message_id=self._message_id,
        )

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video information without downloading. Execute download for gif.

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

            predicted_path = self._download_directory / f"{video_id}.{video_ext}"

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
                if ".gif" in str(e):
                    gifurl = unquote(str(e).split("=")[1])
                    await self._trigger_download_gif(gifurl)
                    return None
                await self.delete_processing_message()
                await self._telegram_bot_controller.send_content_not_found_error(
                    telegram_id=self._telegram_id,
                )
                return None
            raise
