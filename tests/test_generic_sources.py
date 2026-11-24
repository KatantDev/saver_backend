import pytest

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.resolver import SourceResolver
from saver_backend.services.language_resolver import LanguageResolver


@pytest.mark.parametrize(
    ("url", "kind", "code"),
    [
        (
            "https://clips.twitch.tv/FaintLightGullWholeWheat",
            "twitch_clip",
            "FaintLightGullWholeWheat",
        ),
        (
            "https://www.twitch.tv/vanillatv/clip/FaintLightGullWholeWheat?a=1",
            "twitch_clip",
            "FaintLightGullWholeWheat",
        ),
        ("https://m.twitch.tv/videos/2345678901", "twitch_video", "2345678901"),
        (
            "https://medal.tv/games/fortnite/clips/nZ5tEUUDrQXVFWx2g?invite=cr-abc",
            "medal_clip",
            "nZ5tEUUDrQXVFWx2g",
        ),
        (
            "https://medal.tv/ru/clips/nZ5tEUUDrQXVFWx2g",
            "medal_clip",
            "nZ5tEUUDrQXVFWx2g",
        ),
    ],
)
def test_single_videos_of_twitch_and_medal_are_taken(
    url: str,
    kind: str,
    code: str,
) -> None:
    """These links were answered «not supported» although yt-dlp reads them."""
    resolution = SourceResolver().resolve(url)

    assert resolution.source == SourceEnum.GENERIC_YDL
    assert resolution.metadata["type"] == kind
    assert resolution.metadata["code"] == code


@pytest.mark.parametrize(
    "url",
    [
        "https://www.twitch.tv/vanillatv",
        "https://www.twitch.tv/directory/game/Fortnite",
        "https://medal.tv/games/fortnite",
        "https://clips.twitch.tv/a/b",
    ],
)
def test_channels_and_game_pages_are_not_downloads(url: str) -> None:
    """A page without one video stays «not supported»."""
    assert SourceResolver().resolve(url).source == SourceEnum.UNSUPPORTED


@pytest.mark.parametrize("code", ["kk", "fa", "ar", "he", "en", "ru"])
def test_language_has_a_flag(code: str) -> None:
    """Dubbed videos listed Kazakh and Persian tracks without a flag."""
    assert LanguageResolver(code).flag_name
