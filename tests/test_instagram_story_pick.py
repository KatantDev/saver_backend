import base64
from urllib.parse import quote

from saver_backend.services.downloaders.instagram_indown_source import (
    pick_story_links,
    story_media_id,
)


def _photo(media_id: int) -> str:
    key = base64.b64encode(str(media_id).encode()).decode()
    return (
        f"https://scontent.cdninstagram.com/v/{media_id}_n.jpg"
        f"?ig_cache_key={quote(key)}.3-ccb7-5&oh=1"
    )


def _video(name: str) -> str:
    return f"https://scontent.cdninstagram.com/o1/v/t2/f2/m78/{name}.mp4?efg=x&oh=1"


def test_media_id_is_read_from_a_photo_link() -> None:
    """ig_cache_key holds the id seen in /stories/<user>/<id>/."""
    assert story_media_id(_photo(4034074719027640380)) == 4034074719027640380
    assert story_media_id(_video("a")) is None
    assert story_media_id("https://example.com/x.jpg?ig_cache_key=%%%") is None


def test_photo_story_is_matched_exactly() -> None:
    """The BBC News account had 10 photo stories; a link to one sends one."""
    links = [_photo(4033727614227082110 + n) for n in range(10)]

    assert pick_story_links(links, 4033727614227082115) == [links[5]]


def test_video_between_two_photos_is_found() -> None:
    """A video story has no id in its link; its neighbours give it away."""
    links = [_video("a"), _photo(100), _video("b"), _photo(300), _video("c")]

    assert pick_story_links(links, 200) == [_video("b")]


def test_newest_first_order_is_handled() -> None:
    """The same works if indown.io ever lists the newest story first."""
    links = [_video("c"), _photo(300), _video("b"), _photo(100), _video("a")]

    assert pick_story_links(links, 200) == [_video("b")]


def test_last_video_after_the_newest_photo() -> None:
    """A video newer than every photo is the only candidate at the end."""
    links = [_video("a"), _video("b"), _photo(100), _photo(200), _video("c")]

    assert pick_story_links(links, 500) == [_video("c")]


def test_ambiguous_videos_keep_everything() -> None:
    """Two videos with no photo between them cannot be told apart."""
    links = [_video("a"), _video("b"), _photo(300)]

    assert pick_story_links(links, 100) == links
