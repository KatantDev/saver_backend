import pytest

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.resolver import SourceResolver


@pytest.mark.parametrize(
    "url",
    [
        "https://www.pornhub.com/view_video.php?viewkey=abc",
        "https://xvideos.com/video123/title",
        "https://www.redgifs.com/watch/x",
        "https://cdn.xvideos.com/hls/master.m3u8",
    ],
)
def test_adult_sites_are_not_supported(url: str) -> None:
    """Adult links get «not supported», including their .m3u8 streams."""
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
