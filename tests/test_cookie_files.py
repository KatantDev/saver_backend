from pathlib import Path

from saver_backend.services.downloaders.ydl_source import is_cookie_file

COOKIE_LINE = ".example.com\tTRUE\t/\tTRUE\t0\tname\tvalue\n"


def test_netscape_cookie_file_is_used(tmp_path: Path) -> None:
    """A file exported in the Netscape format is loaded by yt-dlp."""
    path = tmp_path / "cookies1.txt"
    path.write_text("# Netscape HTTP Cookie File\n" + COOKIE_LINE)

    assert is_cookie_file(path)


def test_empty_cookie_file_is_skipped(tmp_path: Path) -> None:
    """Prod 23.11: an empty cookies1.txt broke every adult link."""
    path = tmp_path / "cookies1.txt"
    path.write_text("")

    assert not is_cookie_file(path)


def test_file_without_the_header_is_skipped(tmp_path: Path) -> None:
    """yt-dlp refuses a file that does not start with the Netscape header."""
    path = tmp_path / "cookies1.txt"
    path.write_text('[{"name": "a", "value": "b"}]')

    assert not is_cookie_file(path)
