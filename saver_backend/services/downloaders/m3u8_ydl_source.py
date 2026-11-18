import asyncio
import json
import logging
from typing import Any, ClassVar
from urllib.parse import urlparse

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController

# Best video with the best separate audio, or the best stream that has both
M3U8_DEFAULT_FORMAT = "bv*+ba/b"
PROBE_TIMEOUT_SECONDS = 20


class M3U8YdlController(YtDlpController):
    """Asynchronous controller for downloading raw HLS (m3u8) streams via yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.M3U8_YDL
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with yt-dlp parameters for HLS streams."""
        super().__init__(*args, **kwargs)

        params: dict[str, Any] = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
            # Video and separate audio are joined into one mp4 file
            "merge_output_format": "mp4",
        }
        if not self._selected_format_id:
            # Many playlists keep video and audio in separate streams: «best»
            # (one stream with both) does not exist there and yt-dlp fails
            params["format"] = M3U8_DEFAULT_FORMAT
        self._yt_dlp.params.update(params)
        if "format" in params:
            self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
                format_spec=params["format"],
            )

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video info from m3u8 url.

        :param url: The url to get video info from.
        :return: The video info or None.
        """
        info_dict = await super().get_video_info(url=url)
        if info_dict is None or self._video is None:
            return None

        url_path = (
            urlparse(self._resolution.url)
            .path[:200]
            .replace("/", "-")
            .replace(".", "-")
        )
        ext = info_dict.get("ext")

        info_dict.update(
            {
                "id": url_path,
                "title": url_path,
                "fulltitle": url_path,
            },
        )
        # The quality menu is built from these formats
        _add_video_with_audio_formats(info_dict)
        await _add_size_of_single_stream(info_dict)

        self._video = VideoDTO.from_yt_dlp(
            info=info_dict,
            # Same name as yt-dlp writes (outtmpl «%(id)s.<token>.%(ext)s»);
            # without the token the finished file was never found («Error»)
            file_path=self._download_directory
            / f"{url_path}.{self._download_token}.{ext}",
            extract_direct_links=self.DIRECT_URL_DOWNLOAD,
            quality=self._selected_format_id or "best",
        )

        return info_dict


def _add_video_with_audio_formats(info: dict[str, Any]) -> None:
    """
    Offer video streams that have no audio together with the audio stream.

    The menu lists only formats with both video and audio, so a playlist with
    separate audio got «No formats available». Each video stream is added
    once more as «<id>+ba» (yt-dlp joins the best audio to it).

    :param info: The dictionary from yt_dlp.extract_info, changed in place.
    """
    formats: list[dict[str, Any]] = info.get("formats") or []
    if not any(f.get("vcodec") == "none" for f in formats):
        return
    joined = [
        {**f, "format_id": f"{f['format_id']}+ba", "acodec": "joined"}
        for f in formats
        if f.get("acodec") == "none" and f.get("vcodec") not in (None, "none")
    ]
    formats.extend(joined)


async def _add_size_of_single_stream(info: dict[str, Any]) -> None:
    """
    Read the frame size of a playlist with one stream and no size in it.

    A plain (not master) playlist does not state the resolution, so the menu
    had nothing to show. ffprobe reads it from the stream itself.

    :param info: The dictionary from yt_dlp.extract_info, changed in place.
    """
    formats: list[dict[str, Any]] = info.get("formats") or []
    if len(formats) != 1 or formats[0].get("height"):
        return
    size = await _probe_stream_size(formats[0].get("url") or info.get("url", ""))
    if not size:
        return
    width, height = size
    formats[0].update(
        {"width": width, "height": height, "resolution": f"{width}x{height}"},
    )
    info.update({"width": width, "height": height})


async def _probe_stream_size(url: str) -> tuple[int, int] | None:
    """
    Frame size of an HLS stream, read with ffprobe over http(s) only.

    :param url: Playlist URL.
    :return: (width, height) or None.
    """
    if urlparse(url).scheme not in ("http", "https"):
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "http,https,tcp,tls,crypto",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        logging.warning("ffprobe could not start: %s", e)
        return None
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            PROBE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        logging.warning("ffprobe timed out on %s", url)
        return None
    try:
        stream = json.loads(stdout or b"{}").get("streams", [{}])[0]
        return int(stream["width"]), int(stream["height"])
    except ValueError, KeyError, IndexError, TypeError:
        logging.warning("ffprobe could not read the frame size of %s", url)
        return None
