from yt_dlp.utils import RetryManager

from saver_backend.services.downloaders.schema import AudioDTO, VideoDTO
from saver_backend.services.downloaders.ydl_source import _retry_backoff

URL = "https://www.tiktok.com/@user/video/1"


def test_yt_dlp_can_call_the_pause_function() -> None:
    """Prod 23.11: «_retry_backoff() got an unexpected keyword argument 'n'»."""
    delays: list[float] = []

    RetryManager.report_retry(
        OSError("connection reset"),
        2,
        5,
        info=lambda _message: None,
        warn=lambda _message: None,
        sleep_func=_retry_backoff,
        suffix=None,
    )
    delays.extend(_retry_backoff(n=n) for n in range(6))

    assert delays == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]


def test_title_with_angle_bracket_is_escaped() -> None:
    """Prod 22-23.11: a TikTok title with «<3» made Telegram refuse the video."""
    video = VideoDTO(url=URL, source_id="1", title="love u <3 & more")

    assert "love u &lt;3 &amp; more" in video.title_html
    assert "<3" not in video.title_html


def test_channel_and_links_are_escaped() -> None:
    """A channel name or a link cannot break the caption markup either."""
    video = VideoDTO(
        url=URL + '?a=1&b="2"',
        source_id="1",
        title="Title",
        channel="A<B",
        channel_url="https://example.com/c?x=1&y=2",
    )

    caption = video.title_html

    assert "#A&lt;B" in caption
    assert 'href="https://example.com/c?x=1&amp;y=2"' in caption
    assert "a=1&amp;b=&quot;2&quot;" in caption


def test_audio_title_is_escaped() -> None:
    """The same for a track title."""
    audio = AudioDTO(url="https://music.example/t/1", title="Rock <n> Roll")

    assert audio.title_html == (
        "<a href='https://music.example/t/1'>Rock &lt;n&gt; Roll</a>"
    )
