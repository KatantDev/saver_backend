import logging
import re
from abc import ABC, abstractmethod
from typing import Callable, ClassVar, Iterable, Optional, Type, TypeVar
from urllib.parse import parse_qs, urlparse, urlunparse

from saver_backend.entities.enums import InstagramContentTypeEnum, SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.instagram_api_source import (
    InstagramAPIController,
)
from saver_backend.services.downloaders.instagram_ydl_source import (
    InstagramYdlController,
)
from saver_backend.services.downloaders.rutube_ydl_source import (
    RutubeYdlController,
)
from saver_backend.services.downloaders.tiktok_api_source import TikTokAPIController
from saver_backend.services.downloaders.vk_clips_ydl_source import (
    VKClipsYdlController,
)
from saver_backend.services.downloaders.vk_video_ydl_source import (
    VKVideoYdlController,
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
                return Resolution(
                    source=self.SOURCE,
                    url=cleaned_url,
                    metadata={"type": key, "code": match.group("code")},
                )
        return None

    @staticmethod
    def _clean_url(url: str) -> str:
        """
        Clean URL by removing query parameters and fragments.

        For YouTube, it specifically preserves the 'v' parameter.

        :param url: The URL to clean.
        :return: The cleaned URL.
        """
        if "://" not in url:
            url = "https://" + url

        p = urlparse(url)
        query = ""

        if "youtube.com" in p.netloc:
            query_params = parse_qs(p.query)
            if "v" in query_params:
                query = f"v={query_params['v'][0]}"

        return urlunparse((p.scheme or "https", p.netloc, p.path, "", query, ""))


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

    def match(self, url: str) -> Optional[Resolution]:
        if not self._host_in(url, *self.HOSTS):
            return None
        return Resolution(source=self.SOURCE, url=self._clean_url(url))


@register_detector()
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


# @register_detector()
class InstagramInstaloaderDetector(Detector):
    """Detector for Instagram."""

    SOURCE = SourceEnum.INSTAGRAM_API
    CONTROLLER = InstagramAPIController
    HOSTS = (
        "instagram.com",
        "www.instagram.com",
        "m.instagram.com",
    )
    REGEX: ClassVar[dict[str, re.Pattern[str]]] = {
        InstagramContentTypeEnum.POST: re.compile(r"^/p/(?P<code>[A-Za-z0-9_-]+)/?$"),
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

    def match(self, url: str) -> Optional[Resolution]:
        """
        Check if the url is a valid YouTube video url.

        :param url: URL to check.
        :return: Resolution if the url is valid, None otherwise.
        """
        if not self._host_in(url, *self.HOSTS):
            return None

        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)

        if "youtu.be" in parsed.netloc:
            video_code = parsed.path.strip("/")
            if video_code:
                return Resolution(
                    source=self.SOURCE,
                    url=self._clean_url(url),
                    metadata={"code": video_code},
                )

        if parsed.path == "/watch" and "v" in query_params:
            video_code = query_params["v"][0]
            return Resolution(
                source=self.SOURCE,
                url=self._clean_url(url),
                metadata={"code": video_code},
            )

        return None


@register_detector()
class VKVideoDetector(Detector):
    """Detector for VK Video."""

    SOURCE = SourceEnum.VK_VIDEO_YDL
    CONTROLLER = VKVideoYdlController
    HOSTS = (
        "vk.com",
        "m.vk.com",
        "vkvideo.ru",
        "www.vk.com",
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
        if parsed.query:
            match = self._CODE_RE.search(parsed.query)
            if match:
                url = f"{parsed.scheme}://{parsed.netloc}/{match.group(0)}"
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


class SourceResolver:
    """Resolver for source of the message."""

    def __init__(self, detectors: Optional[Iterable[Detector]] = None) -> None:
        detectors_list = list(detectors) if detectors else list(_REGISTRY)
        self._detectors: dict[SourceEnum, Detector] = {
            detector.SOURCE: detector for detector in detectors_list
        }

    def resolve(self, url: str) -> Resolution:
        """
        Resolve the source of the message.

        :param url: URL to check.
        :return: Resolution if the url is a valid url for the detector, None otherwise.
        """
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
