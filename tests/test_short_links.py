import pytest

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.resolver import SourceResolver


@pytest.mark.parametrize(
    ("url", "source", "expected_url", "code"),
    [
        (
            "https://rutube.ru/shorts/a05ae11cd1cd40ec0a415f5a04c906bf/",
            SourceEnum.RUTUBE_YDL,
            "https://rutube.ru/video/a05ae11cd1cd40ec0a415f5a04c906bf/",
            "a05ae11cd1cd40ec0a415f5a04c906bf",
        ),
        (
            "https://rutube.ru/video/f175b08c363fc3bb7a15017795433619/?r=wd",
            SourceEnum.RUTUBE_YDL,
            "https://rutube.ru/video/f175b08c363fc3bb7a15017795433619/",
            "f175b08c363fc3bb7a15017795433619",
        ),
        (
            "https://dzen.ru/shorts/6acbcf74c035b53adc6fefd9?share_to=link",
            SourceEnum.DZEN_YDL,
            "https://dzen.ru/video/watch/6acbcf74c035b53adc6fefd9",
            "6acbcf74c035b53adc6fefd9",
        ),
        (
            "https://dzen.ru/video/watch/6ad100027e6dd4477f322617",
            SourceEnum.DZEN_YDL,
            "https://dzen.ru/video/watch/6ad100027e6dd4477f322617",
            "6ad100027e6dd4477f322617",
        ),
        (
            "https://www.youtube.com/embed/29ixFQIGZaY",
            SourceEnum.YOUTUBE_VIDEO_YDL,
            "https://youtu.be/29ixFQIGZaY",
            "29ixFQIGZaY",
        ),
        (
            "https://www.youtube-nocookie.com/embed/29ixFQIGZaY?start=10",
            SourceEnum.YOUTUBE_VIDEO_YDL,
            "https://youtu.be/29ixFQIGZaY",
            "29ixFQIGZaY",
        ),
        (
            "https://www.youtube.com/watch?v=LHUvedAssyE",
            SourceEnum.YOUTUBE_VIDEO_YDL,
            "https://youtu.be/LHUvedAssyE",
            "LHUvedAssyE",
        ),
        (
            "https://www.youtube.com/shorts/lFXhdlfA2yk",
            SourceEnum.YOUTUBE_SHORTS_YDL,
            "https://www.youtube.com/shorts/lFXhdlfA2yk",
            "lFXhdlfA2yk",
        ),
    ],
)
def test_resolves_short_and_embed_links(
    url: str,
    source: SourceEnum,
    expected_url: str,
    code: str,
) -> None:
    """Shorts and embed links resolve to the page yt-dlp can download."""
    resolution = SourceResolver().resolve(url)

    assert resolution.source == source
    assert resolution.url == expected_url
    assert resolution.metadata["code"] == code


@pytest.mark.parametrize(
    "url",
    [
        "https://dzen.ru/shorts",
        "https://rutube.ru/shorts/",
        "https://www.youtube.com/embed/",
    ],
)
def test_ignores_pages_without_video_id(url: str) -> None:
    """Section pages without a video id are not taken for videos."""
    assert SourceResolver().resolve(url).source == SourceEnum.UNSUPPORTED
