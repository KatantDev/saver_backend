from typing import Any

from saver_backend.services.downloaders.x_ydl_source import (
    fxtwitter_info,
    tweet_id_of,
    video_index_of,
)

URL = "https://x.com/hakase_don/status/2124679054599369125/video/1"


def _payload(count: int = 1) -> dict[str, Any]:
    videos = [
        {
            "url": f"https://video.twimg.com/amplify_video/{n}/vid/avc1/1280x720/a.mp4",
            "thumbnail_url": "https://pbs.twimg.com/thumb.jpg",
            "duration": 516.808,
            "width": 1280,
            "height": 720,
            "type": "video",
        }
        for n in range(1, count + 1)
    ]
    return {
        "code": 200,
        "tweet": {
            "text": "Sushibunniii - Ayaka",
            "author": {"name": "Hakase", "screen_name": "hakase_don"},
            "media": {"videos": videos},
        },
    }


def test_tweet_id_and_video_index_are_read_from_the_link() -> None:
    """…/video/2 points at the second video of the tweet."""
    assert tweet_id_of(URL) == "2124679054599369125"
    assert video_index_of(URL) == 0
    assert video_index_of(URL.replace("/video/1", "/video/2")) == 1
    assert video_index_of("https://x.com/a/status/1669888830126841857") == 0
    assert tweet_id_of("https://x.com/a") is None


def test_video_hidden_from_guests_is_taken_from_fxtwitter() -> None:
    """Prod 23.11: yt-dlp found no video in this tweet, FxTwitter has the mp4."""
    info = fxtwitter_info(_payload(), URL)

    assert info is not None
    assert info["id"] == "2124679054599369125"
    assert info["url"].endswith("/1/vid/avc1/1280x720/a.mp4")
    assert info["title"] == "Sushibunniii - Ayaka"
    assert (info["width"], info["height"], info["duration"]) == (1280, 720, 516.808)
    assert info["webpage_url"] == URL


def test_second_video_of_a_tweet_gets_its_own_id() -> None:
    """Two videos of one tweet must not share a cache record."""
    info = fxtwitter_info(_payload(2), URL.replace("/video/1", "/video/2"))

    assert info is not None
    assert info["id"] == "2124679054599369125_2"
    assert "/2/vid/" in info["url"]


def test_tweet_without_video_gives_nothing() -> None:
    """A text or photo tweet still gets the fixupx link."""
    payload = _payload()
    payload["tweet"]["media"] = {"photos": [{"url": "https://pbs.twimg.com/a.jpg"}]}

    assert fxtwitter_info(payload, URL) is None
    assert fxtwitter_info({"code": 404, "message": "NOT_FOUND"}, URL) is None


def test_multi_line_title_gives_a_one_line_file_name() -> None:
    """Dev 23.11: a tweet's line break in the file name broke the upload."""
    from saver_backend.services.downloaders.schema import VideoDTO

    video = VideoDTO(url=URL, source_id="1", title="first line\nsecond\r\n\tthird")

    assert not any(ord(char) < 32 for char in video.filename)
    assert "first line" in video.filename
