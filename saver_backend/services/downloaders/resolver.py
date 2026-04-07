import logging
import re
from abc import ABC, abstractmethod
from typing import Callable, ClassVar, Iterable, Optional, Type, TypeVar
from urllib.parse import urlparse, urlunparse

from saver_backend.entities.enums import (
    InstagramContentTypeEnum,
    SourceEnum,
    YandexMusicContentTypeEnum,
)
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.adult_ydl_source import AdultYdlController
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.dzen_ydl import (
    DzenYdlController,
)
from saver_backend.services.downloaders.facebook_ydl_source import (
    FacebookYdlController,
)
from saver_backend.services.downloaders.instagram_indown_source import (
    InstagramInDownController,
)
from saver_backend.services.downloaders.instagram_instaloader_source import (
    InstagramInstaloaderController,
)
from saver_backend.services.downloaders.instagram_ydl_source import (
    InstagramYdlController,
)
from saver_backend.services.downloaders.kinovod_ydl_source import (
    KinovodYdlController,
)
from saver_backend.services.downloaders.m3u8_ydl_source import M3U8YdlController
from saver_backend.services.downloaders.ok_ydl_source import (
    OkYdlController,
)
from saver_backend.services.downloaders.pinterest_ydl_source import (
    PinterestYdlController,
)
from saver_backend.services.downloaders.rutube_ydl_source import (
    RutubeYdlController,
)
from saver_backend.services.downloaders.tiktok_api_source import TikTokAPIController
from saver_backend.services.downloaders.vk_api_source import (
    VKAPIController,
)
from saver_backend.services.downloaders.vk_clips_ydl_source import (
    VKClipsYdlController,
)
from saver_backend.services.downloaders.vk_video_ydl_source import (
    VKVideoYdlController,
)
from saver_backend.services.downloaders.x_ydl_source import (
    XYdlController,
)
from saver_backend.services.downloaders.yandex_ymdantic import (
    YmdanticController,
)
from saver_backend.services.downloaders.youtube_shorts_ydl_source import (
    YouTubeShortsYdlController,
)
from saver_backend.services.downloaders.youtube_video_ydl_source import (
    YouTubeVideoYdlController,
)


class Detector(ABC):
    """Detector for source of the message."""

    SOURCE: ClassVar[SourceEnum]
    CONTROLLER: ClassVar[Type[BaseSourceController]]
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {}

    @abstractmethod
    def match(self, url: str) -> Optional[Resolution]:
        """
        Check if the url is a valid url for the detector.

        :param url: URL to check.
        :return: Resolution if the url is a valid url for the detector, None otherwise.
        """
        raise NotImplementedError

    @staticmethod
    def _idna_encode(host: str) -> str:
        try:
            return host.encode("idna").decode("ascii")
        except (UnicodeEncodeError, TypeError, ValueError):
            return host

    def _host_in(self, url: str, *candidates: str) -> bool:
        """
        Check if URL host matches any of the candidate hosts.

        :param url: URL to check.
        :param candidates: Host candidates to match against.
        :return: True if host matches any candidate, False otherwise.
        """
        if not url or not candidates:
            return False

        try:
            # support links without scheme
            if "://" not in url:
                url = "https://" + url

            parsed = urlparse(url)
            if not parsed.netloc:
                return False

            host = self._idna_encode(parsed.netloc.lower().split(":")[0])
            return any(host == c or host.endswith("." + c) for c in candidates)
        except Exception:
            return False

    def _match_regex(self, url: str) -> Optional[Resolution]:
        cleaned_url = self._clean_url(url)
        for key, regex in self.REGEX.items():
            match = regex.match(urlparse(cleaned_url).path)
            if match:
                metadata = match.groupdict()
                metadata["type"] = key
                return Resolution(
                    source=self.SOURCE,
                    url=cleaned_url,
                    metadata=metadata,
                )
        return None

    @staticmethod
    def _clean_url(url: str) -> str:
        """
        Clean URL by removing query parameters and fragments.

        :param url: The URL to clean.
        :return: The cleaned URL.
        """
        if "://" not in url:
            url = "https://" + url

        p = urlparse(url)

        return urlunparse((p.scheme or "https", p.netloc, p.path, "", "", ""))


_REGISTRY: list[Detector] = []

T = TypeVar("T", bound=Type[Detector])


def register_detector() -> Callable[[T], T]:
    """Decorator for registering detector in global registry."""

    def _wrapper(cls: T) -> T:
        inst = cls()  # detectors are static, without state
        _REGISTRY.append(inst)
        return cls

    return _wrapper


@register_detector()
class TikTokDetector(Detector):
    """Detector for TikTok."""

    SOURCE = SourceEnum.TIKTOK
    CONTROLLER = TikTokAPIController
    HOSTS = (
        "tiktok.com",
        "m.tiktok.com",
        "vm.tiktok.com",
        "vt.tiktok.com",
        "www.tiktok.com",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "video": re.compile(r".*/video/(?P<code>\d+)"),
        "short": re.compile(r"^/(?P<code>[A-Za-z0-9]+)/?$"),
    }

    def match(self, url: str) -> Optional[Resolution]:
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


@register_detector()
class InstagramInDownDetector(Detector):
    """Detector for Instagram."""

    SOURCE = SourceEnum.INSTAGRAM_INDOWN
    CONTROLLER = InstagramInDownController
    HOSTS = (
        "instagram.com",
        "www.instagram.com",
        "m.instagram.com",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        InstagramContentTypeEnum.REELS: re.compile(
            r"^/(?:[^/]+/)?reels?/(?P<code>[A-Za-z0-9_-]+)/?$",
        ),
        InstagramContentTypeEnum.POST: re.compile(
            r"^/(?:[^/]+/)?p/(?P<code>[A-Za-z0-9_-]+)/?$",
        ),
    }

    def match(self, url: str) -> Optional[Resolution]:
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


# @register_detector()
class InstagramYdlDetector(Detector):
    """Detector for Instagram."""

    SOURCE = SourceEnum.INSTAGRAM_YDL
    CONTROLLER = InstagramYdlController
    HOSTS = (
        "instagram.com",
        "www.instagram.com",
        "m.instagram.com",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        InstagramContentTypeEnum.REELS: re.compile(
            r"^/(?:[^/]+/)?reels?/(?P<code>[A-Za-z0-9_-]+)/?$",
        ),
    }

    def match(self, url: str) -> Optional[Resolution]:
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


@register_detector()
class InstagramInstaloaderDetector(Detector):
    """Detector for Instagram."""

    SOURCE = SourceEnum.INSTAGRAM_INSTALOADER
    CONTROLLER = InstagramInstaloaderController
    HOSTS = (
        "instagram.com",
        "www.instagram.com",
        "m.instagram.com",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        InstagramContentTypeEnum.POST: re.compile(
            r"^/(?:[^/]+/)?p/(?P<code>[A-Za-z0-9_-]+)/?$",
        ),
        InstagramContentTypeEnum.STORIES: re.compile(
            r"^/stories/(?P<user>[A-Za-z0-9._]+)/(?P<code>\d+)/?$",
        ),
    }

    def match(self, url: str) -> Optional[Resolution]:
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


@register_detector()
class YouTubeShortsDetector(Detector):
    """Detector for YouTube Shorts."""

    SOURCE = SourceEnum.YOUTUBE_SHORTS_YDL
    CONTROLLER = YouTubeShortsYdlController
    HOSTS = (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "shorts": re.compile(r"^/shorts/(?P<code>[A-Za-z0-9_-]+)/?$"),
    }

    def match(self, url: str) -> Optional[Resolution]:
        """
        Check if the url is a valid YouTube Shorts url.

        :param url: URL to check.
        :return: Resolution if the url is valid, None otherwise.
        """
        if not self._host_in(url, *self.HOSTS):
            return None

        # Handle full /shorts/ links
        return self._match_regex(url)


@register_detector()
class VKClipsDetector(Detector):
    """Detector for VK Clips."""

    SOURCE = SourceEnum.VK_CLIPS_YDL
    CONTROLLER = VKClipsYdlController
    HOSTS = (
        "vk.com",
        "m.vk.com",
        "www.vk.com",
        "vk.ru",
        "m.vk.ru",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "clips": re.compile(r"^/clip(?P<code>-?\d+_\d+)/?$"),
    }

    _CODE_RE: ClassVar[re.Pattern[str]] = re.compile(r"clip-?\d+_\d+")

    def match(self, url: str) -> Optional[Resolution]:
        """
        Check if the url is a valid VK Clip url.

        :param url: URL to check.
        :return: Resolution if the url is valid, None otherwise.
        """
        if not self._host_in(url, *self.HOSTS):
            return None

        parsed = urlparse(url)

        if parsed.query:
            match = self._CODE_RE.search(parsed.query)
            if match:
                url = f"{parsed.scheme}://{parsed.netloc}/{match.group(0)}"

        return self._match_regex(url)


@register_detector()
class PinterestDetector(Detector):
    """Detector for Pinterest videos."""

    SOURCE = SourceEnum.PINTEREST_YDL
    CONTROLLER = PinterestYdlController
    HOSTS = (
        "pinterest.com",
        "pin.it",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "pin": re.compile(r"/pin/(?P<code>\d+)"),
        "short": re.compile(r"^/(?P<code>[a-zA-Z0-9]+)$"),
    }

    def match(self, url: str) -> Optional[Resolution]:
        """Check if the url is a valid Pinterest pin url."""
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


@register_detector()
class XDetector(Detector):
    """Detector for X / Twitter."""

    SOURCE = SourceEnum.X_YDL
    CONTROLLER = XYdlController
    HOSTS = (
        "x.com",
        "twitter.com",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "status": re.compile(r"^/(?:[^/]+)/status/(?P<code>\d+)"),
    }

    def match(self, url: str) -> Resolution | None:
        """Check if the url is a valid X status url."""
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


@register_detector()
class YouTubeVideoDetector(Detector):
    """Detector for standard YouTube videos."""

    SOURCE = SourceEnum.YOUTUBE_VIDEO_YDL
    CONTROLLER = YouTubeVideoYdlController
    HOSTS = (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "short": re.compile(r"^/(?P<code>[a-zA-Z0-9_-]{11})$"),
        "live": re.compile(r"^/live/(?P<code>[a-zA-Z0-9_-]{11})$"),
    }

    _CODE_RE: ClassVar[re.Pattern[str]] = re.compile(r"v=([A-Za-z0-9_-]{11})")

    def match(self, url: str) -> Optional[Resolution]:
        """
        Check for a valid YouTube video URL using various regex patterns.

        :param url: URL to check.
        :return: Resolution if the url is valid, None otherwise.
        """
        if not self._host_in(url, *self.HOSTS):
            return None

        parsed = urlparse(url)
        match = self._CODE_RE.search(parsed.query)
        if parsed.query and match:
            url = f"https://youtu.be/{match.group(1)}"

        return self._match_regex(url)


@register_detector()
class VKVideoDetector(Detector):
    """Detector for VK Video."""

    SOURCE = SourceEnum.VK_VIDEO_YDL
    CONTROLLER = VKVideoYdlController
    HOSTS = (
        "vkvideo.ru",
        "vk.com",
        "m.vk.com",
        "www.vk.com",
        "m.vk.ru",
        "vk.ru",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "video_path": re.compile(r"/video(?P<code>-?\d+_\d+)"),
    }

    _PLAYLIST_REGEX: ClassVar[re.Pattern[str]] = re.compile(
        r"/playlist/[^/?#]+/(video-?\d+_\d+)",
    )
    _CODE_RE: ClassVar[re.Pattern[str]] = re.compile(r"video-?\d+_\d+")

    def match(self, url: str) -> Optional[Resolution]:
        if not self._host_in(url, *self.HOSTS):
            return None

        parsed = urlparse(url)
        match = self._PLAYLIST_REGEX.search(parsed.path)
        if match:
            url = f"{parsed.scheme}://{parsed.netloc}/{match.group(1)}"

        match = self._CODE_RE.search(parsed.query)
        if match:
            url = f"{parsed.scheme}://{parsed.netloc}/{match.group(0)}"
        return self._match_regex(url)


@register_detector()
class VKAPIDetector(Detector):
    """
    Unified detector for VK Wall posts and Photos.

    Matches:
    - vk.com/wall-123_456 -> type='wall'
    - vk.com/photo-123_456 -> type='photo'
    """

    SOURCE = SourceEnum.VK_API_YDL
    CONTROLLER = VKAPIController
    HOSTS = (
        "vk.com",
        "m.vk.com",
        "www.vk.com",
        "vk.ru",
        "m.vk.ru",
        "w.vk.com",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "wall": re.compile(r"^/wall(?P<code>-?\d+_\d+)"),
        "photo": re.compile(r"^/photo(?P<code>-?\d+_\d+)"),
    }

    def match(self, url: str) -> Optional[Resolution]:
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


@register_detector()
class RutubeDetector(Detector):
    """Detector for Rutube videos."""

    SOURCE = SourceEnum.RUTUBE_YDL
    CONTROLLER = RutubeYdlController
    HOSTS = (
        "rutube.ru",
        "www.rutube.ru",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "video": re.compile(r"^/video/(?P<code>[a-zA-Z0-9]+)/?"),
        "embed": re.compile(r"^/play/embed/(?P<code>[a-zA-Z0-9]+)/?"),
    }

    def match(self, url: str) -> Optional[Resolution]:
        """Check if the url is a valid Rutube video url."""
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


@register_detector()
class AdultDetector(Detector):
    """Detector for various adult websites supported by yt-dlp."""

    SOURCE = SourceEnum.ADULT_YDL
    CONTROLLER = AdultYdlController
    HOSTS = (
        "pornhub.com",
        "pornotube.com",
        "nuvid.com",
        "beeg.com",
        "empflix.com",
        "eporner.com",
        "eroprofile.com",
        "lovehomeporn.com",
        "manyvids.com",
        "motherless.com",
        "moviefap.com",
        "nubiles-porn.com",
        "nubiles.net",
        "redgifs.com",
        "redtube.com",
        "rule34video.com",
        "sunporno.com",
        "thisvid.com",
        "tnaflix.com",
        "txxx.com",
        "xnxx.com",
        "xvideos.com",
        "xxxymovies.com",
        "youjizz.com",
        "youporn.com",
        "zenporn.com",
    )

    def match(self, url: str) -> Optional[Resolution]:
        """Check if the url is from a supported adult website."""
        if not self._host_in(url, *self.HOSTS):
            return None
        return Resolution(source=self.SOURCE, url=url)


@register_detector()
class M3U8Detector(Detector):
    """Detector for Rutube videos."""

    SOURCE = SourceEnum.M3U8_YDL
    CONTROLLER = M3U8YdlController

    def match(self, url: str) -> Optional[Resolution]:
        """
        Check if the url is a valid Rutube video url.

        :param url: URL to check.
        """
        if not url.endswith(".m3u8"):
            return None
        return Resolution(source=self.SOURCE, url=self._clean_url(url))


@register_detector()
class DzenDetector(Detector):
    """Detector for Dzen videos."""

    SOURCE = SourceEnum.DZEN_YDL
    CONTROLLER = DzenYdlController
    HOSTS = (
        "dzen.ru",
        "www.dzen.ru",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "video": re.compile(r"^/video/watch/(?P<code>[a-zA-Z0-9_-]+)"),
    }

    def match(self, url: str) -> Optional[Resolution]:
        """Check if the url is a valid Dzen video url."""
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


@register_detector()
class OkDetector(Detector):
    """Detector for ok.ru videos."""

    SOURCE = SourceEnum.OK_YDL
    CONTROLLER = OkYdlController
    HOSTS = (
        "ok.ru",
        "www.ok.ru",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "video": re.compile(r"^/video/(?P<code>\d+)"),
    }

    def match(self, url: str) -> Optional[Resolution]:
        """Check if the url is a valid ok.ru video url."""
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


@register_detector()
class FacebookDetector(Detector):
    """Detector for Facebook videos."""

    SOURCE = SourceEnum.FACEBOOK_YDL
    CONTROLLER = FacebookYdlController
    HOSTS = (
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "fb.watch",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "videos": re.compile(r"^/(?:[^/]+)/videos/(?P<code>\d+)/?$"),
        "reel": re.compile(r"^/reel/(?P<code>\d+)/?$"),
        "share": re.compile(r"^/share/v/(?P<code>[^/]+)/?$"),
    }
    _WATCH_RE: ClassVar[re.Pattern[str]] = re.compile(r"v=(\d+)")

    def match(self, url: str) -> Optional[Resolution]:
        """Check if the url is a valid Facebook video/reel url."""
        if not self._host_in(url, *self.HOSTS):
            return None

        parsed = urlparse(url)
        match = self._WATCH_RE.search(parsed.query)
        if parsed.path == "/watch/" and match:
            url = f"https://www.facebook.com/author/videos/{match.group(1)}"
        return self._match_regex(url)


@register_detector()
class KinovodDetector(Detector):
    """Detector for Kinovod videos."""

    SOURCE = SourceEnum.KINOVOD_YDL
    CONTROLLER = KinovodYdlController
    HOSTS = (
        "kinovod.pro",
        "www.kinovod.pro",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        "film": re.compile(r"^/film/(?P<code>[^/]+)(?:/.*)?$"),
        "tv_show": re.compile(r"^/tv_show/(?P<code>[^/]+)(?:/.*)?$"),
        "serial": re.compile(r"^/serial/(?P<code>[^/]+)(?:/.*)?$"),
        "trailer": re.compile(r"^/trailer/(?P<code>[^/]+)(?:/.*)?$"),
    }

    def match(self, url: str) -> Optional[Resolution]:
        """Check if the url is a valid Kinovod video/serial url."""
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


@register_detector()
class YmDanticDetector(Detector):
    """Detector for Yandex Music ymdantic."""

    SOURCE = SourceEnum.YMDANTIC
    CONTROLLER = YmdanticController
    HOSTS = (
        "music.yandex.ru",
        "music.yandex.com",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        YandexMusicContentTypeEnum.TRACK: re.compile(
            r".*/track/(?P<code>\d+)(?:[?#]|$)",
        ),
        YandexMusicContentTypeEnum.ALBUM: re.compile(
            r".*/album/(?P<code>\d+)(?:[?#]|$)",
        ),
    }

    def match(self, url: str) -> Optional[Resolution]:
        """Check if the url is a valid Yandex album/track url."""
        if not self._host_in(url, *self.HOSTS):
            return None
        return self._match_regex(url)


class SourceResolver:
    """Resolver for source of the message."""

    _URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)

    def __init__(self, detectors: Optional[Iterable[Detector]] = None) -> None:
        detectors_list = list(detectors) if detectors else list(_REGISTRY)
        self._detectors: dict[SourceEnum, Detector] = {
            detector.SOURCE: detector for detector in detectors_list
        }

    def resolve(self, text: str) -> Resolution:
        """
        Resolve the source of the message.

        :param text: Message text to check.
        :return: Resolution if the url is a valid url for the detector, None otherwise.
        """
        url_match = self._URL_RE.search(text)
        url = url_match.group(1) if url_match else text
        for detector in self._detectors.values():
            res = detector.match(url)
            if res is not None:
                return res
        return Resolution(source=SourceEnum.UNSUPPORTED, url=url)

    def get_controller(
        self,
        source: str | SourceEnum | Resolution,
    ) -> Type[BaseSourceController] | None:
        """
        Get controller for source.

        :param source: Source.
        :return: Controller.
        """
        if isinstance(source, str):
            source = self.resolve(source)
        if isinstance(source, Resolution):
            source = source.source
        logging.info("Getting controller for %s", source)
        controller = self._detectors[source].CONTROLLER
        if controller is None or controller is BaseSourceController:
            return None
        return controller
