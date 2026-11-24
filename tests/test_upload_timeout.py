from pathlib import Path

from aiogram.methods import SendMediaGroup, SendMessage, SendVideo
from aiogram.types import FSInputFile, InputMediaVideo, URLInputFile

from saver_backend.services.telegram.upload_session import (
    MEGABYTE,
    upload_size,
    upload_timeout,
)


def _file(tmp_path: Path, name: str, megabytes: int) -> FSInputFile:
    path = tmp_path / name
    with path.open("wb") as file:
        file.truncate(megabytes * MEGABYTE)
    return FSInputFile(path=path)


def test_requests_without_files_keep_the_usual_limit() -> None:
    """Messages and files sent by URL are not given extra time."""
    assert upload_timeout(SendMessage(chat_id=1, text="hi"), 180) == 180
    by_url = SendVideo(chat_id=1, video=URLInputFile(url="https://example.com/v.mp4"))
    assert upload_timeout(by_url, 180) == 180


def test_small_upload_keeps_the_usual_limit(tmp_path: Path) -> None:
    """A small file fits into the usual limit."""
    method = SendVideo(chat_id=1, video=_file(tmp_path, "small.mp4", 20))
    assert upload_timeout(method, 180) == 180


def test_big_upload_gets_more_time(tmp_path: Path) -> None:
    """A 1.5 GB file gets about half an hour instead of three minutes."""
    method = SendVideo(
        chat_id=1,
        video=_file(tmp_path, "big.mp4", 1500),
        cover=_file(tmp_path, "cover.jpg", 1),
    )
    assert upload_size(method) == 1501 * MEGABYTE
    assert upload_timeout(method, 180) == 120 + 1501


def test_media_group_counts_every_file(tmp_path: Path) -> None:
    """Files of an album are added up."""
    method = SendMediaGroup(
        chat_id=1,
        media=[
            InputMediaVideo(media=_file(tmp_path, "a.mp4", 200)),
            InputMediaVideo(media=_file(tmp_path, "b.mp4", 300)),
        ],
    )
    assert upload_size(method) == 500 * MEGABYTE
    assert upload_timeout(method, 180) == 620


def test_missing_file_is_ignored(tmp_path: Path) -> None:
    """A file that is already gone does not break the request."""
    method = SendVideo(chat_id=1, video=FSInputFile(path=tmp_path / "gone.mp4"))
    assert upload_timeout(method, 180) == 180
