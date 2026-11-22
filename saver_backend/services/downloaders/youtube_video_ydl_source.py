import asyncio
import logging
from typing import Any, ClassVar

from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController

# H.264 + AAC in mp4, up to 1080p (vertical too)
YOUTUBE_H264_FORMAT = (
    "bv*[vcodec^=avc1][height<=1920][width<=1920]+ba[ext=m4a]"
    "/b[vcodec^=avc1][ext=mp4]"
    "/bv*[height<=1920][width<=1920]+ba"
    "/b"
)


# yt-dlp player clients tried in turn. On 20.11 the default client got 403/400
# on media through 6-7 of 8 prod routes for a video, tv_simply passed 21 of 24
# (video x route) checks in 1080p H.264; the default stays as the next try.
YOUTUBE_PLAYER_CLIENTS: tuple[tuple[str, ...], ...] = (("tv_simply",), ("default",))


def youtube_extractor_args(clients: tuple[str, ...]) -> dict[str, Any]:
    """
    Build yt-dlp extractor_args for YouTube with the given player clients.

    :param clients: Player clients for yt-dlp.
    :return: extractor_args with the PO token provider and the clients.
    """
    return {
        "youtubepot-bgutilhttp": {"base_url": ["http://saver_backend-bgutil:4416"]},
        "youtube": {"player_client": list(clients)},
    }


class YouTubeClientRotationMixin:
    """Switch the yt-dlp player client on every retry of a YouTube download."""

    _yt_dlp: Any
    _client_index: int = 0

    def _next_player_client(self) -> None:
        self._client_index = (self._client_index + 1) % len(YOUTUBE_PLAYER_CLIENTS)
        clients = YOUTUBE_PLAYER_CLIENTS[self._client_index]
        self._yt_dlp.params["extractor_args"] = youtube_extractor_args(clients)

    def _switch_proxy_for_retry(self) -> None:
        super()._switch_proxy_for_retry()  # type: ignore[misc]
        self._next_player_client()


def _is_combined(fmt: dict[str, Any]) -> bool:
    """
    Tell whether a format has both a known video and a known audio codec.

    YouTube now adds format 773, a 6-channel audio track whose codecs yt-dlp
    leaves empty. Counted as video+audio it made a second "combined" format,
    the merge of video-only streams with audio was skipped and the menu of a
    4K video offered only 360p.
    """
    return fmt.get("vcodec") not in (None, "none") and fmt.get("acodec") not in (
        None,
        "none",
    )


def _is_h264(fmt: dict[str, Any]) -> bool:
    return str(fmt.get("vcodec") or "").startswith("avc1")


class YouTubeVideoYdlController(YouTubeClientRotationMixin, YtDlpController):
    """Asynchronous controller for downloading videos from YouTube through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.YOUTUBE_VIDEO_YDL
    # Prod drops ~1 in 5 YouTube downloads once while a retry passes (task 24)
    RETRIES_ON_ERROR: ClassVar[int] = 1
    # A blocked proxy gives 403 while the server itself can reach YouTube
    DIRECT_FALLBACK: ClassVar[bool] = True
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for YouTube."""
        super().__init__(*args, **kwargs)

        youtube_params = {
            "format": YOUTUBE_H264_FORMAT,
            "merge_output_format": "mp4",
            "extractor_args": youtube_extractor_args(YOUTUBE_PLAYER_CLIENTS[0]),
            "remote_components": ["ejs:github"],
        }
        self._cookie_fallback = False
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=youtube_params["format"]
        )
        self._yt_dlp.params.update(youtube_params)

    def _remove_combined_formats(
        self, formats: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        combined_formats = [fmt for fmt in formats if _is_combined(fmt)]

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
                and fmt.get("audio_ext") == "m4a"
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

        # Keep only H.264 qualities if available
        video_only_mp4 = [
            fmt
            for fmt in formats
            if fmt.get("video_ext") == "mp4"
            and fmt.get("acodec") == "none"
            and fmt.get("vcodec") != "none"
        ]
        h264_only = any(_is_h264(fmt) for fmt in video_only_mp4)

        # Modify formats: combine mp4 video-only with best audio
        modified_formats = []
        for fmt in formats:
            if fmt in video_only_mp4 and (not h264_only or _is_h264(fmt)):
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
                or "403: Forbidden" in str(e)
            ):
                logging.warning(
                    f"[youtup] proxy: {self._yt_dlp.params['proxy']};"
                    f" url: {self._resolution.url}; error: {e!s}"
                )
                if self.DIRECT_FALLBACK and self._proxy and not self._proxies:
                    self._switch_to_direct()
                else:
                    self._set_proxy()
                self._next_player_client()
                return await self.get_video_info(url=url)
            if "Unsupported URL" in str(e) or "HTTP Error 404" in str(e):
                await self.delete_processing_message()
                await self._telegram_bot_controller.send_content_not_found_error(
                    telegram_id=self._telegram_id,
                )
                return None
            if (
                "Sign in" in str(e)
                and not self._cookie_fallback
                and not self._base_options.get("cookiefile")
            ):
                self._cookie_fallback = True
                params = self._yt_dlp.params
                self._yt_dlp.close()
                del self._yt_dlp
                params.get("extractor_args", {}).update(
                    {
                        "youtube": {
                            "player_client": ["mweb"],
                        }
                    }
                )
                self._set_cookies(force_set=self._cookie_fallback)
                params.pop("proxy")
                self._yt_dlp = self._create_yt_dlp(params)
                self._set_proxy()
                return await self.get_video_info(url=url)
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
