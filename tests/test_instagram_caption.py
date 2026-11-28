import pytest

from saver_backend.services.downloaders.schema import VideoDTO

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
