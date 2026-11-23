import pytest

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.resolver import SourceResolver


@pytest.mark.parametrize(
    "url",
    [
        "https://www.pornhub.com/view_video.php?viewkey=abc",
        "https://www.pornhub.com/gay/video?viewkey=abc",
        "https://xvideos.com/video123/title",
        "https://www.redgifs.com/watch/x",
        "https://fr.pornhub.org/view_video.php?viewkey=abc",
        "https://xhamster.com/videos/title-xhPYisO",
        "https://ru.xhamster46.desi/videos/title-xhPYisO",
    ],
)
def test_adult_sites_are_supported(url: str) -> None:
    """Bulat 23.11: adult sites are downloaded whatever the category."""
    assert SourceResolver().resolve(url).source == SourceEnum.ADULT_YDL


@pytest.mark.parametrize(
    "url",
    [
        "https://xnxxxxxarab.gay/video/123",
        "https://www.xnxxxxxarab.gay/video/123",
        "https://cdn.xnxxxxxarab.gay/hls/master.m3u8",
    ],
)
def test_the_blocked_site_is_not_supported(url: str) -> None:
    """The one site Bulat asked to turn off, including its .m3u8 streams."""
    assert SourceResolver().resolve(url).source == SourceEnum.UNSUPPORTED


@pytest.mark.parametrize(
    ("url", "source"),
    [
        ("https://youtu.be/ErAqN6gXqZQ", SourceEnum.YOUTUBE_VIDEO_YDL),
        ("https://example.com/live/stream.m3u8", SourceEnum.M3U8_YDL),
    ],
)
def test_other_sites_are_untouched(url: str, source: SourceEnum) -> None:
    """Other sources keep resolving as before."""
    assert SourceResolver().resolve(url).source == source
