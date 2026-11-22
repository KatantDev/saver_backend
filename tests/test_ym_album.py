import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from saver_backend.services.downloaders import yandex_ymdantic
from saver_backend.services.downloaders.schema import AudioDTO
from saver_backend.services.downloaders.yandex_ymdantic import YmdanticController

TRACK_IDS = list(range(1, 12))


def _dto(track_id: int) -> AudioDTO:
    return AudioDTO(source_id=str(track_id), title=f"track {track_id}")


def _source(get_track: object) -> MagicMock:
    source = MagicMock()
    source._get_track = get_track  # noqa: SLF001
    source._fetch_album_tracks = (  # noqa: SLF001
        lambda ids, album_id: YmdanticController._fetch_album_tracks(  # noqa: SLF001
            source, ids, album_id
        )
    )
    album = MagicMock()
    album.volumes = [[MagicMock(id=t) for t in TRACK_IDS[:6]]]
    album.volumes.append([MagicMock(id=t) for t in TRACK_IDS[6:]])
    source._client.get_album_with_tracks = AsyncMock(return_value=album)  # noqa: SLF001
    return source


@pytest.fixture(autouse=True)
def no_pause() -> object:
    """Retries go without a pause in tests."""
    with patch.object(yandex_ymdantic, "ALBUM_RETRY_PAUSE_SECONDS", 0):
        yield


@pytest.mark.anyio
async def test_tracks_that_failed_once_are_fetched_again() -> None:
    """Tracks failing on the first try are not lost (9 of 11 in the audit)."""
    failed_once: set[int] = set()

    async def get_track(track_id: int, _album_id: str) -> AudioDTO | None:
        if track_id in {7, 9} and track_id not in failed_once:
            failed_once.add(track_id)
            return None
        return _dto(track_id)

    source = _source(get_track)

    dtos = await YmdanticController._get_album_tracks(source, 3389008)  # noqa: SLF001

    assert [dto.source_id for dto in dtos] == [str(t) for t in TRACK_IDS]


@pytest.mark.anyio
async def test_unavailable_track_is_tried_three_times_then_skipped() -> None:
    """A track that never downloads does not block the rest of the album."""
    calls: dict[int, int] = {}

    async def get_track(track_id: int, _album_id: str) -> AudioDTO | None:
        calls[track_id] = calls.get(track_id, 0) + 1
        return None if track_id == 5 else _dto(track_id)

    source = _source(get_track)

    dtos = await YmdanticController._get_album_tracks(source, 1)  # noqa: SLF001

    assert len(dtos) == len(TRACK_IDS) - 1
    assert calls[5] == yandex_ymdantic.ALBUM_TRACK_ATTEMPTS
    assert calls[1] == 1


@pytest.mark.anyio
async def test_album_is_fetched_a_few_tracks_at_a_time() -> None:
    """At most ALBUM_FETCH_CONCURRENCY track requests run at once."""
    running = 0
    peak = 0

    async def get_track(track_id: int, _album_id: str) -> AudioDTO:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return _dto(track_id)

    source = _source(get_track)

    await YmdanticController._get_album_tracks(source, 1)  # noqa: SLF001

    assert peak == yandex_ymdantic.ALBUM_FETCH_CONCURRENCY


def _album_source(tracks: list[AudioDTO]) -> MagicMock:
    source = MagicMock()
    source._resolution.metadata = {"code": "4988025", "type": "album"}  # noqa: SLF001
    source._download_album_in_flac = AsyncMock(return_value=False)  # noqa: SLF001
    source._get_album_tracks = AsyncMock(return_value=tracks)  # noqa: SLF001
    source._has_any_flac_in_album = AsyncMock(return_value=True)  # noqa: SLF001
    source._send_audio_album = AsyncMock()  # noqa: SLF001
    source._store_album_info_to_fsm = AsyncMock()  # noqa: SLF001
    source._send_flac_button = AsyncMock()  # noqa: SLF001
    source._send_error_message = AsyncMock()  # noqa: SLF001
    return source


@pytest.mark.anyio
async def test_one_track_album_with_flac_sends_no_error() -> None:
    """An album of one track sends the track with the FLAC button, no «Error»."""
    track = _dto(38831880)
    source = _album_source([track])

    await YmdanticController.download_video(source)

    source._send_audio_album.assert_not_awaited()  # noqa: SLF001
    source._send_error_message.assert_not_awaited()  # noqa: SLF001
    source._store_album_info_to_fsm.assert_awaited_once_with([track])  # noqa: SLF001
    source._send_flac_button.assert_awaited_once()  # noqa: SLF001


@pytest.mark.anyio
async def test_album_with_flac_sends_all_but_the_last_track_as_a_group() -> None:
    """The last track still goes separately, with the FLAC button."""
    tracks = [_dto(t) for t in TRACK_IDS]
    source = _album_source(tracks)

    await YmdanticController.download_video(source)

    source._send_audio_album.assert_awaited_once_with(tracks[:-1])  # noqa: SLF001
    source._send_flac_button.assert_awaited_once()  # noqa: SLF001
    source._send_error_message.assert_not_awaited()  # noqa: SLF001


@pytest.mark.anyio
async def test_single_track_is_sent_as_a_downloaded_file(tmp_path: object) -> None:
    """Prod: Telegram-side fetch of the Yandex link timed out, so download first."""
    source = MagicMock()
    source.CODECSEPARATOR = YmdanticController.CODECSEPARATOR
    path = f"{tmp_path}/1.mp3"
    source._download_audio_file = AsyncMock(return_value=path)  # noqa: SLF001
    source._send_audio = AsyncMock()  # noqa: SLF001
    dto = AudioDTO(source_id="1", title="t", media_url="https://cdn.example/1")

    await YmdanticController._send_audio_track(source, dto)  # noqa: SLF001

    source._download_audio_file.assert_awaited_once()  # noqa: SLF001
    sent = source._send_audio.await_args.args[0]  # noqa: SLF001
    assert str(sent.path) == path
    assert sent.media_url is None


@pytest.mark.anyio
async def test_album_tracks_are_downloaded_before_sending(tmp_path: object) -> None:
    """MP3 album tracks used to go to Telegram as links too."""
    source = MagicMock()
    source._check_dto_in_cache = AsyncMock(return_value=None)  # noqa: SLF001
    source._download_audio_file = AsyncMock(  # noqa: SLF001
        side_effect=lambda dto, name: f"{tmp_path}/{name}"
    )
    source._send_audio_group = AsyncMock()  # noqa: SLF001
    dtos = [
        AudioDTO(source_id=str(n), title=f"t{n}", media_url=f"https://cdn.example/{n}")
        for n in (1, 2)
    ]

    await YmdanticController._send_audio_album(source, dtos)  # noqa: SLF001

    sent = source._send_audio_group.await_args.args[0]  # noqa: SLF001
    assert [d.media_url for d in sent] == [None, None]
    assert all(d.path for d in sent)


@pytest.mark.anyio
async def test_dead_proxy_download_is_retried_through_another(
    tmp_path: Path,
) -> None:
    """Prod 21.11: 2 of 12 proxies never reach Yandex's CDN."""

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("dead proxy", request=request)

    def alive(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ID3 audio bytes")

    source = MagicMock()
    source._download_directory = tmp_path  # noqa: SLF001
    source._proxy = "proxy-dead"  # noqa: SLF001
    source._proxies = ["proxy-alive"]  # noqa: SLF001
    source._http_client = httpx.AsyncClient(transport=httpx.MockTransport(dead))  # noqa: SLF001
    real_client = httpx.AsyncClient
    used: list[str | None] = []

    def client_for(**kwargs: object) -> httpx.AsyncClient:
        used.append(kwargs.get("proxy"))  # type: ignore[arg-type]
        return real_client(transport=httpx.MockTransport(alive))

    dto = AudioDTO(source_id="1", title="t", media_url="https://cdn.example/1.mp3")
    with patch.object(yandex_ymdantic.httpx, "AsyncClient", side_effect=client_for):
        path = await YmdanticController._download_audio_file(source, dto, "1.mp3")  # noqa: SLF001

    assert path is not None
    assert path.read_bytes() == b"ID3 audio bytes"
    assert used == ["proxy-alive"]
