from yt_dlp.extractor.vk import VKBaseIE

from saver_backend.services.downloaders import vk_video_ydl_source  # noqa: F401
from saver_backend.services.downloaders.vk_ydl_patch import (
    DEFAULT_SUBTITLE_TITLE,
    apply_vk_subtitle_patch,
    fix_subtitle_titles,
)


def test_subtitle_without_a_title_gets_one() -> None:
    """VK sends «title: null»; yt-dlp then fails on .split()."""
    payload = [
        "0",
        [
            {
                "player": {
                    "params": [
                        {
                            "subs": [
                                {"lang": "ru", "title": None, "url": "https://x/1"},
                                {"lang": "en", "url": "https://x/2"},
                                {
                                    "lang": "de",
                                    "title": "auto.vtt",
                                    "url": "https://x/3",
                                },
                            ],
                        },
                    ],
                },
            },
        ],
    ]

    subs = fix_subtitle_titles(payload)[1][0]["player"]["params"][0]["subs"]

    assert [sub["title"] for sub in subs] == [
        DEFAULT_SUBTITLE_TITLE,
        DEFAULT_SUBTITLE_TITLE,
        "auto.vtt",
    ]
    assert all(sub["title"].split(".")[-1] for sub in subs)


def test_other_answers_are_left_alone() -> None:
    """Answers without subtitles, and non-container values, pass through."""
    assert fix_subtitle_titles(None) is None
    assert fix_subtitle_titles({"subs": None, "a": [1, "x"]}) == {
        "subs": None,
        "a": [1, "x"],
    }


def test_the_extractor_is_patched_once() -> None:
    """Importing the VK controller patches yt-dlp; a second call changes nothing."""
    patched = VKBaseIE._download_payload  # noqa: SLF001

    apply_vk_subtitle_patch()

    assert VKBaseIE._download_payload is patched  # noqa: SLF001
    assert getattr(patched, "_saver_subtitle_patch", False) is True
