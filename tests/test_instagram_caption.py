import pytest

from saver_backend.services.downloaders.instagram_ydl_source import (
    InstagramYdlController,
)
from saver_backend.services.downloaders.schema import VideoDTO, caption_description

REEL = "https://www.instagram.com/reel/Defe6L1PJVR/?fqnt=dzNrZDV5dnpucXpw"


@pytest.mark.parametrize(
    "title",
    ["Video by danicainthedesert", "Post by bmw_x4_444", "Story by some.name"],
)
def test_instagram_placeholder_title_gives_the_link(title: str) -> None:
    """Bulat 28.11: no «Video by name», the caption is the link as before."""
    video = VideoDTO(
        url=REEL,
        source_id="Defe6L1PJVR",
        title=title,
        channel="danicainthedesert",
        channel_url="https://www.instagram.com/danicainthedesert",
    )

    assert "by" not in video.title_html
    assert video.title_html == REEL.replace("&", "&amp;") + "\n"


def test_instagram_real_title_stays() -> None:
    """A title that is not made up from the author is shown as usual."""
    video = VideoDTO(url=REEL, source_id="x", title="Morning walk")

    assert "Morning walk" in video.title_html


def test_same_words_elsewhere_stay() -> None:
    """Only Instagram's made-up titles are dropped."""
    video = VideoDTO(
        url="https://www.youtube.com/watch?v=1",
        source_id="1",
        title="Video by John",
    )

    assert "Video by John" in video.title_html


RECIPE = "Pasta with tomatoes.\nBoil the pasta 8 minutes, add tomatoes & basil <3"


def test_instagram_caption_has_the_post_text() -> None:
    """Bulat 28.11: the text of the post goes under the link, folded."""
    video = VideoDTO(
        url=REEL,
        source_id="Defe6L1PJVR",
        title="Video by danicainthedesert",
        description=RECIPE,
    )

    caption = video.title_html

    assert caption.startswith(REEL.replace("&", "&amp;") + "\n")
    assert "<blockquote expandable>Pasta with tomatoes." in caption
    assert "tomatoes &amp; basil &lt;3</blockquote>" in caption
    assert "Video by" not in caption


def test_long_post_text_is_cut() -> None:
    """The caption stays under Telegram's 1024 characters."""
    video = VideoDTO(
        url=REEL,
        source_id="x",
        title="Video by someone",
        description="word " * 500,
    )

    text = caption_description(video.description)

    assert len(text) < 900
    assert text.endswith("…</blockquote>\n")


@pytest.mark.parametrize("description", [None, "", "  \n "])
def test_no_post_text_gives_the_link_only(description: str | None) -> None:
    """A post without text keeps the caption a bare link."""
    video = VideoDTO(
        url=REEL,
        source_id="x",
        title="Video by someone",
        description=description,
    )

    assert video.title_html == REEL.replace("&", "&amp;") + "\n"


def test_carousel_items_get_the_post_text() -> None:
    """Photos and videos of a carousel carry the text of the whole post."""
    controller = InstagramYdlController.__new__(InstagramYdlController)
    controller._video_from_item = lambda item, url: VideoDTO(  # type: ignore[method-assign] # noqa: SLF001
        url=url,
        source_id=str(item["id"]),
        title="Video by someone",
    )
    info = {
        "id": "post",
        "description": RECIPE,
        "entries": [
            {"id": "1", "thumbnails": [{"url": "https://cdn/p1.jpg"}]},
            {"id": "2", "formats": [{"format_id": "f"}]},
        ],
    }

    media = controller._media_from_info(info, REEL)  # noqa: SLF001

    assert [item.description for item in media] == [RECIPE, RECIPE]
