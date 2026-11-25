import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any, ClassVar

from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import FsmKeysEnum, SourceEnum
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.ydl_source import (
    YtDlpController,
    is_connection_failure,
    is_cookie_file,
)

# H.264 + AAC in mp4, up to 1080p (vertical too)
YOUTUBE_H264_FORMAT = (
    "bv*[vcodec^=avc1][height<=1920][width<=1920]+ba[ext=m4a]"
    "/b[vcodec^=avc1][ext=mp4]"
    "/bv*[height<=1920][width<=1920]+ba"
    "/b"
)


# yt-dlp player clients tried in turn. Prod 23.11: tv_simply (put first on 20.11)
# now returns a single 360p format, so the menu offered only 360p and a quality
# picked from a menu built by another client was «not available» (59 failures in
# two days). The default client and web_embedded return up to 1080p and their
# files download; the «Sign in to confirm you're not a bot» answer depends on the
# proxy, not on the client.
YOUTUBE_PLAYER_CLIENTS: tuple[tuple[str, ...], ...] = (
    ("default",),
    ("web_embedded",),
)

# What YouTube answers through an address it has flagged.
BOT_CHECK_TEXT = "Sign in to confirm"
# What it answers for an age-restricted video, through any address.
AGE_CHECK_TEXT = "confirm your age"
# How long a flagged proxy is left out for YouTube.
BLOCKED_PROXY_PAUSE_SECONDS = 3 * 60 * 60
# Proxies tried for one request before the server's own address.
MAX_PROXY_SWITCHES = 2
# A levelled «-drc» audio track loses to the original of the same bitrate.
DRC_PENALTY_KBPS = 1


# The same picture under three ids: the https stream of the default client, the
# HLS stream and the HLS file with sound that web_embedded returns instead.
# By itag, not by height: itag 299 of a vertical video is 1920 pixels high.
FORMAT_TWINS: dict[str, tuple[str, str]] = {
    "160": ("269", "91"),
    "133": ("229", "92"),
    "134": ("230", "93"),
    "135": ("231", "94"),
    "136": ("232", "95"),
    "137": ("270", "96"),
    "298": ("311", "300"),
    "299": ("312", "301"),
}
STREAM_OF_COMBINED = {
    combined: stream for stream, (_hls, combined) in FORMAT_TWINS.items()
}
PICTURE_HEIGHTS = {
    "160": 144,
    "133": 240,
    "134": 360,
    "135": 480,
    "136": 720,
    "137": 1080,
    "298": 720,
    "299": 1080,
}

# What YouTube answers when the video exists but not for the country of the
# address the request came from. Some proxies are seen as Russian addresses,
# the server itself is in the Netherlands: 21 such refusals in three days were
# for videos that open from the server.
REGION_REFUSAL_HINTS = (
    "available in your country",
    "claimed content",
    "video is not available",
    "video unavailable",
)
# Answers that mean the video is closed or gone for everyone
GONE_HINTS = (
    "private video",
    "video unavailable",
    "has been removed",
    "has been terminated",
)
# «This video is not available», with nothing after it, is not about the video:
# YouTube gives it to a session it does not like, and the same video opens a
# moment later. On 24-25.11 it was taken for a removed video and 20 users were
# told «private or deleted» about public videos.
PASSING_REFUSAL_RE = re.compile(r"this video is not available\s*$", re.IGNORECASE)
# Fresh sessions tried for such a refusal
MAX_PASSING_REFUSAL_RETRIES = 2
PASSING_REFUSAL_PAUSE = 1.0
# Size of one request for a part of the file. YouTube slows a long single
# request down until it times out («Read timed out» after 10 retries, 23
# downloads lost on the evening of 24.11); requests by parts are not slowed.
YOUTUBE_CHUNK_SIZE = 10 * 1024 * 1024


def is_region_refusal(error: BaseException) -> bool:
    """Whether the refusal may depend on the country of the address."""
    text = str(error).lower()
    return any(hint in text for hint in REGION_REFUSAL_HINTS)


def is_passing_refusal(error: BaseException) -> bool:
    """Whether YouTube refused this session, not the video."""
    return bool(PASSING_REFUSAL_RE.search(str(error).strip()))


def is_gone(error: BaseException) -> bool:
    """Whether YouTube says the video is private, removed or blocked."""
    text = str(error).lower()
    # a country block and a passing «try again later» are not about the video
    if "available in your country" in text or "try again later" in text:
        return False
    return any(hint in text for hint in GONE_HINTS)


def download_format_chain(selected: str) -> str:
    """
    Formats to try for a quality picked in the menu, the picked one first.

    The menu and the download are two separate requests to YouTube and may get
    different format lists: a repeated request after a 403 went through another
    player client, which has neither the picked id nor the «-N» suffix yt-dlp
    adds to audio tracks of dubbed videos, and failed with «Requested format is
    not available» (66 failures in three days). The same quality is therefore
    also asked for under its other ids.

    :param selected: Format id from the menu («137+140-0», «96», «95-0»).
    :return: A yt-dlp format string.
    """
    video, _plus, audio = selected.partition("+")
    itag = video.split("-")[0]
    chain = [selected]
    if audio and itag in FORMAT_TWINS:
        hls, combined = FORMAT_TWINS[itag]
        chain += [
            f"{video}+ba[ext=m4a]",
            f"{hls}+ba[ext=m4a]",
            f"b[format_id^={combined}]",
        ]
    elif not audio and itag in STREAM_OF_COMBINED:
        stream = STREAM_OF_COMBINED[itag]
        chain += [
            f"b[format_id^={itag}]",
            f"{stream}+ba[ext=m4a]",
            f"{FORMAT_TWINS[stream][0]}+ba[ext=m4a]",
        ]
    else:
        return selected
    height = PICTURE_HEIGHTS[itag if audio else STREAM_OF_COMBINED[itag]]
    # Last resort: the closest H.264 picture not above the picked quality.
    # Limited on both sides, the smaller side of a vertical video is its width.
    chain.append(
        f"bv*[vcodec^=avc1][height<={height}]+ba[ext=m4a]"
        f"/bv*[vcodec^=avc1][width<={height}]+ba[ext=m4a]",
    )
    return "/".join(dict.fromkeys(chain))


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


def proxy_key(proxy: str | None) -> str:
    """Short hash of a proxy, so the shared state never keeps its address."""
    return hashlib.sha1((proxy or "direct").encode()).hexdigest()[:12]  # noqa: S324


def is_bot_check(error: BaseException) -> bool:
    """
    Whether YouTube refused the request because of the address it came from.

    «Sign in to confirm your age» starts the same way but is about the video:
    counted as a bot check, every age-restricted video took three proxies out
    of use for three hours.
    """
    text = str(error)
    return BOT_CHECK_TEXT in text and AGE_CHECK_TEXT not in text


def is_age_restricted(error: BaseException) -> bool:
    """Whether YouTube gives the video only to a signed-in adult account."""
    return AGE_CHECK_TEXT in str(error)


class YouTubeClientRotationMixin:
    """
    Routes and player clients for YouTube.

    On prod 4 of 12 proxies got «Sign in to confirm you're not a bot» for every
    video and every client (23.11), while the other proxies and the server's own
    address worked. A flagged proxy is remembered for a few hours and left out,
    so a request does not spend its tries on it.
    """

    _yt_dlp: Any
    _proxy: str | None
    _proxies: list[str]
    _telegram_bot_controller: Any
    DIRECT_FALLBACK: ClassVar[bool]
    _client_index: int = 0
    _routes_ready: bool = False
    _route_switches: int = 0

    def _next_player_client(self) -> None:
        self._client_index = (self._client_index + 1) % len(YOUTUBE_PLAYER_CLIENTS)
        clients = YOUTUBE_PLAYER_CLIENTS[self._client_index]
        self._yt_dlp.params["extractor_args"] = youtube_extractor_args(clients)

    _passing_refusals: int = 0

    def _switch_proxy_for_retry(self) -> None:
        # The client stays: another one has other format ids («Requested
        # format is not available» on the second attempt, for a menu quality
        # and for shorts alike), and the usual reason of a failed download is
        # a passing 403 or a timeout, not the client.
        super()._switch_proxy_for_retry()  # type: ignore[misc]

    async def _retry_passing_refusal(self, error: BaseException) -> bool:
        """
        Start a fresh session when YouTube refused this one.

        :param error: The refusal.
        :return: True when the request should be repeated.
        """
        if not is_passing_refusal(error):
            return False
        if self._passing_refusals >= MAX_PASSING_REFUSAL_RETRIES:
            return False
        self._passing_refusals += 1
        logging.warning(
            "YouTube refused the session (%s), fresh session %s of %s",
            error,
            self._passing_refusals,
            MAX_PASSING_REFUSAL_RETRIES,
        )
        self._next_player_client()
        self._yt_dlp = self._create_yt_dlp(self._yt_dlp.params)  # type: ignore[attr-defined]
        await asyncio.sleep(PASSING_REFUSAL_PAUSE)
        return True

    def _retry_from_server(self, error: BaseException) -> bool:
        """
        Go to the server's own address when the proxy's country may be the reason.

        :param error: The refusal YouTube gave through the proxy.
        :return: True when the request should be repeated.
        """
        if not self._proxy or not self.DIRECT_FALLBACK:
            return False
        if not is_region_refusal(error):
            return False
        logging.warning(
            "YouTube refused the request through a proxy, trying the server: %s",
            error,
        )
        self._switch_to_direct()  # type: ignore[attr-defined]
        return True

    async def _blocked_proxies(self) -> set[str]:
        data = await self._telegram_bot_controller.get_fsm_data(
            user_id=int(FsmKeysEnum.YOUTUBE_BLOCKED_PROXIES),
            chat_id=int(FsmKeysEnum.YOUTUBE_BLOCKED_PROXIES),
        )
        now = time.time()
        return {
            key
            for key, until in (data or {}).items()
            if isinstance(until, (int, float)) and until > now
        }

    async def _prepare_routes(self) -> None:
        """Leave out the proxies YouTube flagged recently; done once per request."""
        if self._routes_ready:
            return
        self._routes_ready = True
        if not self._proxy:
            return
        blocked = await self._blocked_proxies()
        pool = [self._proxy, *self._proxies]
        fresh = [proxy for proxy in pool if proxy_key(proxy) not in blocked]
        if len(fresh) == len(pool):
            return
        logging.info(
            "YouTube: %s of %s proxies are flagged, left out",
            len(pool) - len(fresh),
            len(pool),
        )
        if fresh:
            self._proxies = fresh
            self._set_proxy()  # type: ignore[attr-defined]
        elif self.DIRECT_FALLBACK:
            self._switch_to_direct()  # type: ignore[attr-defined]

    async def _note_bot_check(self, error: BaseException) -> None:
        """Remember the proxy YouTube has just refused."""
        if not self._proxy or not is_bot_check(error):
            return
        logging.warning("YouTube flagged a proxy, it is left out for a while")
        await self._telegram_bot_controller.update_fsm_data(
            user_id=int(FsmKeysEnum.YOUTUBE_BLOCKED_PROXIES),
            chat_id=int(FsmKeysEnum.YOUTUBE_BLOCKED_PROXIES),
            data={proxy_key(self._proxy): time.time() + BLOCKED_PROXY_PAUSE_SECONDS},
        )

    def _next_route(self) -> bool:
        """
        Move to another proxy, then to the server's own address.

        :return: False when the request already goes from the server's address.
        """
        if not self._proxy:
            return False
        if self._proxies and self._route_switches < MAX_PROXY_SWITCHES:
            self._route_switches += 1
            self._set_proxy()  # type: ignore[attr-defined]
        elif self.DIRECT_FALLBACK:
            self._switch_to_direct()  # type: ignore[attr-defined]
        else:
            return False
        self._next_player_client()
        return True


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


def _pick_audio(formats: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Pick the m4a audio track to merge with video-only formats.

    The higher bitrate wins. The «-drc» twin of a track is the same audio with
    its loudness levelled (140-drc was picked over 140 on prod): the original
    comes first.

    :param formats: Formats from yt-dlp.
    :return: The audio format, None when the video has no m4a audio.
    """
    best_audio = None
    best_audio_priority = 0.0
    for fmt in formats:
        if (
            fmt.get("acodec") != "none"
            and fmt.get("vcodec") == "none"
            and fmt.get("video_ext") == "none"
            and fmt.get("audio_ext") == "m4a"
        ):
            abr = fmt.get("abr") or 0
            if "drc" in str(fmt.get("format_id")):
                abr -= DRC_PENALTY_KBPS
            if abr > best_audio_priority:
                best_audio_priority = abr
                best_audio = fmt
    return best_audio


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
            "http_chunk_size": YOUTUBE_CHUNK_SIZE,
        }
        self._cookie_fallback = False
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=youtube_params["format"]
        )
        self._yt_dlp.params.update(youtube_params)

    def _has_cookie_files(self) -> bool:
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        cookie_dir = base_dir / "cookies" / self.SOURCE.value
        return any(is_cookie_file(path) for path in cookie_dir.glob("cookies*.txt"))

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

        best_audio = _pick_audio(formats)

        if not best_audio:
            logging.warning("[youtup] Video dont have audio")
            return info_dict

        best_audio_id = best_audio.get("format_id")
        audio_size = (
            best_audio.get("filesize") or best_audio.get("filesize_approx") or 0
        )
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
                # The file the user gets is video plus audio: both go into the
                # size shown in the menu and checked against Telegram's limit.
                for key in ("filesize", "filesize_approx"):
                    if modified_fmt.get(key):
                        modified_fmt[key] += audio_size
                modified_formats.append(modified_fmt)
            else:
                modified_formats.append(fmt)

        info_dict["formats"] = modified_formats

        return info_dict

    async def get_video_info(  # noqa: PLR0911 - one return per YouTube answer
        self,
        url: str,
    ) -> dict[str, Any] | None:
        """
        Get video information without downloading.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        """
        await self._prepare_routes()
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
                or is_bot_check(e)
                or is_connection_failure(e)
            ):
                logging.warning(
                    "[youtube] the route failed for %s: %s",
                    self._resolution.url,
                    e,
                )
                await self._note_bot_check(e)
                if self._next_route():
                    return await self.get_video_info(url=url)
            if self._retry_from_server(e):
                return await self.get_video_info(url=url)
            if await self._retry_passing_refusal(e):
                return await self.get_video_info(url=url)
            if "Unsupported URL" in str(e) or "HTTP Error 404" in str(e) or is_gone(e):
                logging.info("YouTube video is closed or gone: %s (%s)", url, e)
                await self.delete_processing_message()
                await self._telegram_bot_controller.send_content_not_found_error(
                    telegram_id=self._telegram_id,
                )
                return None
            if is_age_restricted(e) and (
                self._cookie_fallback or not self._has_cookie_files()
            ):
                # Only an account opens it; say so instead of the general error
                logging.error("YouTube video is age-restricted: %s", url)
                await self.delete_processing_message()
                await self._telegram_bot_controller.send_age_restricted_error(
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
                if self._proxies:
                    self._set_proxy()
                return await self.get_video_info(url=url)
            raise

    async def download_video(self) -> None:
        """
        Download a video, checking the cache first.

        If a cached version (file_id) exists, it sends it directly.
        Otherwise, it proceeds with the full download process.
        """

        if self._selected_format_id:
            self._yt_dlp.params.update(
                {
                    "format": download_format_chain(self._selected_format_id),
                    "merge_output_format": "mp4",
                }
            )
            self._yt_dlp = self._create_yt_dlp(self._yt_dlp.params)

        await super().download_video()
