from unittest.mock import AsyncMock, MagicMock

import pytest

from saver_backend.services.downloaders.seekinai_source import SeekinAiController


@pytest.mark.anyio
async def test_failed_download_tells_the_user() -> None:
    """Prod 21.11: a Kwai video failed to download and the user saw 72% forever."""
    source = MagicMock()
    source._resolution.metadata = {"code": "5213629630362183094"}  # noqa: SLF001
    source.send_video_from_cache = AsyncMock(return_value=False)
    source._download_video_by_url = AsyncMock(return_value=None)  # noqa: SLF001
    source._send_error_message = AsyncMock()  # noqa: SLF001
    source._send_video = AsyncMock()  # noqa: SLF001
    data = MagicMock()
    data.medias = [MagicMock(url="https://cdn.example/v.mp4")]

    await SeekinAiController._handle_video(source, data)  # noqa: SLF001

    source._send_error_message.assert_awaited_once()  # noqa: SLF001
    source._send_video.assert_not_awaited()  # noqa: SLF001
