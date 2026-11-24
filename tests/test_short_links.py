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


@pytest.mark.parametrize(
    ("url", "source", "expected_url", "code"),
    [
        (
            "https://vt.tiktok.com/ZS9PAqC7VJdt9-Ljo6m/",
            SourceEnum.TIKTOK,
            "https://vt.tiktok.com/ZS9PAqC7VJdt9-Ljo6m/",
            "ZS9PAqC7VJdt9-Ljo6m",
        ),
        (
            "https://www.tiktok.com/t/ZS9PUobDBuQ5U-vX0wH/",
            SourceEnum.TIKTOK,
            "https://www.tiktok.com/t/ZS9PUobDBuQ5U-vX0wH/",
            "ZS9PUobDBuQ5U-vX0wH",
        ),
        (
            "https://www.facebook.com/share/r/14x8Uuc8Pxi/",
            SourceEnum.FACEBOOK_YDL,
            "https://www.facebook.com/share/r/14x8Uuc8Pxi/",
            "14x8Uuc8Pxi",
        ),
        (
            "https://vkvideo.ru/clip-217896273_456248899",
            SourceEnum.VK_CLIPS_YDL,
            "https://vkvideo.ru/clip-217896273_456248899",
            "-217896273_456248899",
        ),
        (
            "https://m.vk.ru/clips/club217731003/feed/clip-217731003_456239960?chc=0",
            SourceEnum.VK_CLIPS_YDL,
            "https://m.vk.ru/clip-217731003_456239960",
            "-217731003_456239960",
        ),
        (
            "https://fixupx.com/pigstube_/status/2123884336277848075",
            SourceEnum.X_YDL,
            "https://x.com/pigstube_/status/2123884336277848075",
            "2123884336277848075",
        ),
        (
            "https://vxtwitter.com/user/status/2123884336277848075?s=20",
            SourceEnum.X_YDL,
            "https://x.com/user/status/2123884336277848075",
            "2123884336277848075",
        ),
        (
            "https://www.instagram.com/tv/Cf1OIA7IVqq/?avpt=abc",
            SourceEnum.INSTAGRAM_INDOWN,
            "https://www.instagram.com/reel/Cf1OIA7IVqq/",
            "Cf1OIA7IVqq",
        ),
        (
            "https://youtube.com/playlist?list=RD0QEsdm12qeM&playnext=1&si=abc",
            SourceEnum.YOUTUBE_VIDEO_YDL,
            "https://youtu.be/0QEsdm12qeM",
            "0QEsdm12qeM",
        ),
        (
            "https://music.youtube.com/watch?v=0QEsdm12qeM&si=abc",
            SourceEnum.YOUTUBE_VIDEO_YDL,
            "https://youtu.be/0QEsdm12qeM",
            "0QEsdm12qeM",
        ),
    ],
)
def test_resolves_shapes_that_were_answered_not_supported(
    url: str,
    source: SourceEnum,
    expected_url: str,
    code: str,
) -> None:
    """Link shapes users sent on 21-23.11 and got «not supported» for."""
    resolution = SourceResolver().resolve(url)

    assert resolution.source == source
    assert resolution.url == expected_url
    assert resolution.metadata["code"] == code


@pytest.mark.parametrize(
    "url",
    [
        "https://www.tiktok.com/@floareamrcan?_r=1",
        "https://vk.ru/album-122802848_236432361",
        "https://fixupx.com/pigstube_",
        "https://www.facebook.com/share/p/18cZyfHoC2/",
        "https://youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
        "https://youtube.com/playlist?list=RDEMc9FbyB7dAJ5Hxk8JEFbyBQ&playnext=1",
    ],
)
def test_pages_without_one_video_stay_unsupported(url: str) -> None:
    """Profiles, albums and text posts are still answered «not supported»."""
    assert SourceResolver().resolve(url).source == SourceEnum.UNSUPPORTED


RUTUBE_PRIVATE = (
    "https://rutube.ru/video/private/884fb55f07a97ab673c7d654553e0f48/"
    "?p=x2QojCumHTS3rsKHWXN8Lg"
)


@pytest.mark.parametrize(
    ("url", "source", "expected_url"),
    [
        (
            "https://vkvideo.ru/video-84793390_456240139?list=ln-dyjFLd7HfrIVJTeA9O",
            SourceEnum.VK_VIDEO_YDL,
            "https://vkvideo.ru/video-84793390_456240139?list=ln-dyjFLd7HfrIVJTeA9O",
        ),
        (
            "https://vk.ru/live-174596225_456240380",
            SourceEnum.VK_VIDEO_YDL,
            "https://vk.ru/video-174596225_456240380",
        ),
        (
            "https://vk.com/feed?z=photo-1_456239017%2Falbum-1_00%2Frev",
            SourceEnum.VK_API_YDL,
            "https://vk.com/photo-1_456239017",
        ),
        (
            RUTUBE_PRIVATE,
            SourceEnum.RUTUBE_YDL,
            RUTUBE_PRIVATE,
        ),
        (
            "https://rutube.ru/video/884fb55f07a97ab673c7d654553e0f48/?r=wd",
            SourceEnum.RUTUBE_YDL,
            "https://rutube.ru/video/884fb55f07a97ab673c7d654553e0f48/",
        ),
    ],
)
def test_access_keys_and_other_shapes_are_kept(
    url: str,
    source: SourceEnum,
    expected_url: str,
) -> None:
    """«Access by link» videos need their key; live and photo links are read."""
    resolution = SourceResolver().resolve(url)

    assert resolution.source == source
    assert resolution.url == expected_url


def test_rutube_private_link_keeps_its_own_id() -> None:
    """«private» is not the video id."""
    resolution = SourceResolver().resolve(
        "https://rutube.ru/video/private/884fb55f07a97ab673c7d654553e0f48/?p=abcd1234",
    )
    assert resolution.metadata["code"] == "884fb55f07a97ab673c7d654553e0f48"
    assert resolution.metadata["type"] == "private"


def test_strange_access_key_is_dropped() -> None:
    """Only a plain key goes back into the link."""
    resolution = SourceResolver().resolve(
        "https://vkvideo.ru/video-1_2?list=a%20b%3Cscript%3E",
    )
    assert resolution.url == "https://vkvideo.ru/video-1_2"
