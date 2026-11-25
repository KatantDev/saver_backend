# ruff: noqa: SLF001
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import URLInputFile
from vkbottle_types.objects import WallWallpostAttachmentType
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders import vk_common
from saver_backend.services.downloaders.exceptions import HostedElsewhereError
from saver_backend.services.downloaders.resolver import SourceResolver
from saver_backend.services.downloaders.schema import (
    PhotoDTO,
    every_quality_is_oversized,
)
from saver_backend.services.downloaders.vk_api_source import VKAPIController
from saver_backend.services.downloaders.vk_common import hosted_elsewhere
from saver_backend.services.downloaders.vk_video_ydl_source import VKVideoYdlController
from saver_backend.services.downloaders.vk_ydl_patch import (
    HOSTED_ELSEWHERE_MARK,
    embedded_video,
)
from saver_backend.services.downloaders.ydl_source import YtDlpController
from saver_backend.services.telegram.bot_controller import TelegramBotController
from saver_backend.task_manager import tasks

BLOCKED = "ERROR: [vk] This video is unavailable because its creator has been blocked"
OTHER_COUNTRY = "ERROR: [vk] This video is not available in your country"
SIGN_IN = "ERROR: [vk] Video only available to signed-in users"
FOLLOWERS = "ERROR: [vk] Video only available to followers"
VIDEO_URL = "https://vk.com/video-1_2"


def _vk(proxy: str | None = "socks5://ru") -> Any:
    controller: Any = VKVideoYdlController.__new__(VKVideoYdlController)
    controller._telegram_id = 1
    controller._proxy = proxy
    controller._telegram_bot_controller = MagicMock()
    controller._telegram_bot_controller.send_content_not_found_error = AsyncMock()
    controller.delete_processing_message = AsyncMock()
    controller._yt_dlp = MagicMock(params={})
    controller._create_yt_dlp = MagicMock(return_value=MagicMock(params={}))

    def go_direct() -> None:
        controller._proxy = ""

    controller._switch_to_direct = MagicMock(side_effect=go_direct)
    return controller


@pytest.mark.anyio
@pytest.mark.parametrize("refusal", [BLOCKED, OTHER_COUNTRY])
async def test_video_hidden_from_russia_is_asked_without_a_proxy(refusal: str) -> None:
    """Through a Russian proxy VK says «blocked»; from outside the video opens."""
    controller = _vk()
    info = {"id": "-1_2"}
    base = AsyncMock(side_effect=[DownloadError(refusal), info])

    with patch.object(YtDlpController, "get_video_info", base):
        result = await controller.get_video_info(VIDEO_URL)

    assert result == info
    controller._switch_to_direct.assert_called_once()
    send = controller._telegram_bot_controller.send_content_not_found_error
    send.assert_not_awaited()


@pytest.mark.anyio
async def test_creator_blocked_everywhere_is_private_or_deleted() -> None:
    """The second refusal, from outside Russia, is final: one honest answer."""
    controller = _vk()
    base = AsyncMock(side_effect=DownloadError(BLOCKED))

    with patch.object(YtDlpController, "get_video_info", base):
        result = await controller.get_video_info(VIDEO_URL)

    assert result is None
    assert base.await_count == 2
    send = controller._telegram_bot_controller.send_content_not_found_error
    assert send.await_args.kwargs["reason"] == "not_found"


@pytest.mark.anyio
async def test_other_country_refusal_without_a_proxy_goes_up() -> None:
    """Nothing to switch to when the request already goes without a proxy."""
    controller = _vk(proxy="")
    base = AsyncMock(side_effect=DownloadError(OTHER_COUNTRY))

    with (
        patch.object(YtDlpController, "get_video_info", base),
        pytest.raises(DownloadError),
    ):
        await controller.get_video_info(VIDEO_URL)

    assert base.await_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("refusal", [SIGN_IN, FOLLOWERS])
async def test_login_only_video_without_cookies_is_closed(
    refusal: str,
    tmp_path: Path,
) -> None:
    """No account: the user reads «private», the journal says a login is needed."""
    controller = _vk()
    base = AsyncMock(side_effect=DownloadError(refusal))

    with (
        patch.object(vk_common, "VK_COOKIES_DIR", tmp_path),
        patch.object(YtDlpController, "get_video_info", base),
    ):
        result = await controller.get_video_info(VIDEO_URL)

    assert result is None
    send = controller._telegram_bot_controller.send_content_not_found_error
    assert send.await_args.kwargs["reason"] == "login_required"


@pytest.mark.anyio
async def test_followers_only_video_is_tried_with_the_account(tmp_path: Path) -> None:
    """VK's own player calls most «followers only» videos «signed-in users only»."""
    controller = _vk()
    (tmp_path / "cookies1.txt").write_text(
        "# Netscape HTTP Cookie File\n.vk.com\tTRUE\t/\tTRUE\t0\ta\tb\n",
    )
    info = {"id": "-1_2"}
    base = AsyncMock(side_effect=[DownloadError(FOLLOWERS), info])

    with (
        patch.object(vk_common, "VK_COOKIES_DIR", tmp_path),
        patch.object(YtDlpController, "get_video_info", base),
    ):
        result = await controller.get_video_info(VIDEO_URL)

    assert result == info
    assert "cookiefile" in controller._create_yt_dlp.call_args.args[0]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            f"ERROR: [vk] {HOSTED_ELSEWHERE_MARK}https://ok.ru/video/3577707498219",
            "https://ok.ru/video/3577707498219",
        ),
        (
            "ERROR: [youtube] 6sFFJ0gefCA: Sign in to confirm you’re not a bot.",
            "https://youtu.be/6sFFJ0gefCA",
        ),
        ("ERROR: [vk] Access restricted", None),
        ("ERROR: [vk] -1_2: No video formats found!", None),
    ],
)
def test_host_of_an_embedded_video_is_read_from_the_error(
    text: str,
    expected: str | None,
) -> None:
    """The error of yt-dlp tells where the video really lives."""
    assert hosted_elsewhere(text) == expected


@pytest.mark.anyio
async def test_embedded_video_is_handed_to_its_own_site() -> None:
    """A YouTube player inside a VK page: YouTube's refusal is not VK's failure."""
    controller = _vk()
    base = AsyncMock(
        side_effect=DownloadError("ERROR: [youtube] 6sFFJ0gefCA: Video unavailable"),
    )

    with (
        patch.object(YtDlpController, "get_video_info", base),
        pytest.raises(HostedElsewhereError) as moved,
    ):
        await controller.get_video_info(VIDEO_URL)

    assert moved.value.url == "https://youtu.be/6sFFJ0gefCA"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("extractor", "expected"),
    [
        ("Youtube", "https://youtu.be/abc"),
        ("Odnoklassniki", "https://ok.ru/videoembed/abc"),
    ],
)
async def test_embedded_video_read_through_vk_is_handed_over_too(
    extractor: str,
    expected: str,
) -> None:
    """Even when yt-dlp got through, the site's own source does the download."""
    controller = _vk()
    base = AsyncMock(return_value={"id": "abc", "extractor_key": extractor})

    with (
        patch.object(YtDlpController, "get_video_info", base),
        pytest.raises(HostedElsewhereError) as moved,
    ):
        await controller.get_video_info(VIDEO_URL)

    assert moved.value.url == expected


@pytest.mark.anyio
async def test_video_of_vk_itself_is_returned() -> None:
    """An ordinary VK video is not handed anywhere."""
    controller = _vk()
    info = {"id": "-1_2", "extractor_key": "VK"}
    base = AsyncMock(return_value=info)

    with patch.object(YtDlpController, "get_video_info", base):
        assert await controller.get_video_info(VIDEO_URL) == info


def test_ok_video_behind_a_page_without_a_player_is_found() -> None:
    """VK answers without «player», the ok.ru frame sits in the page markup."""
    frame = '<iframe src="https:\\/\\/ok.ru\\/videoembed\\/3577707498219?autoplay=1">'
    payload = ["0", frame, {"mvData": {"title": "t"}, "player_unavailable": 0}]

    assert embedded_video(payload) == "https://ok.ru/videoembed/3577707498219"


def test_youtube_video_behind_a_vk_page_is_found() -> None:
    """VK marks the player as YouTube's and gives the id of the video."""
    player = {
        "type": "youtube",
        "params": [{"extra": "21", "extra_data": "if7RxBhgEj4"}],
    }
    payload = ["0", "<div></div>", {"mvData": {"title": "t"}, "player": player}]

    assert embedded_video(payload) == "https://youtu.be/if7RxBhgEj4"


@pytest.mark.parametrize(
    "payload",
    [
        # VK plays the video itself; a link to ok.ru in a comment means nothing
        [
            "0",
            "see https://ok.ru/videoembed/1234567",
            {"mvData": {}, "player": {"params": [{"hls": "https://vk/x.m3u8"}]}},
        ],
        # VK's own player: «extra_data» there is a channel, not a YouTube id
        [
            "0",
            "<div></div>",
            {
                "mvData": {},
                "player": {"type": "vk", "params": [{"extra_data": "channel1234"}]},
            },
        ],
        # not an answer to «show this video»
        ["0", "ok.ru/videoembed/1234567", {"playlist": []}],
        ["0", "ok.ru/videoembed/1234567"],
        # no player and no frame: the extractor reports it its own way
        ["0", "<div></div>", {"mvData": {}}],
        None,
    ],
)
def test_other_vk_answers_are_not_taken_for_an_embed(payload: Any) -> None:
    """Only a page without a player and with an ok.ru frame is an embed."""
    assert embedded_video(payload) is None


def _format(height: int, kbps: int, protocol: str = "m3u8_native") -> dict[str, Any]:
    return {
        "format_id": f"{protocol}-{height}",
        "ext": "mp4",
        "vcodec": "avc1",
        "acodec": "mp4a",
        "width": height * 16 // 9,
        "height": height,
        "protocol": protocol,
        "tbr": kbps,
    }


def test_thirty_hour_recording_is_over_the_limit_in_every_quality() -> None:
    """144p of a 30-hour video is already over 2 GB."""
    info = {
        "id": "1",
        "duration": 108587,
        "formats": [_format(144, 190), _format(240, 300), _format(720, 1500)],
    }

    assert every_quality_is_oversized(info) is True


@pytest.mark.parametrize(
    "info",
    [
        {"id": "1", "duration": 600, "formats": [_format(144, 190)]},
        # one quality fits
        {
            "id": "1",
            "duration": 30000,
            "formats": [_format(144, 190), _format(1080, 4000)],
        },
        # nothing is known about sizes
        {"id": "1", "formats": [{"format_id": "x", "vcodec": "avc1", "acodec": "a"}]},
        {"id": "1", "formats": []},
        {"id": "1"},
    ],
)
def test_video_with_a_quality_that_fits_is_not_too_long(info: dict[str, Any]) -> None:
    """«Too long» is said only when every known size is over the limit."""
    assert every_quality_is_oversized(info) is False


@pytest.mark.parametrize(
    ("url", "code"),
    [
        (
            (
                "https://vkvideo.ru/video_ext.php?autoplay=1&hash=3857199faeac5fb2"
                "&id=456239087&loop=0&oid=-29440301&js_api=1"
            ),
            "-29440301_456239087",
        ),
        (
            "https://vk.ru/video_ext.php?oid=787385537&id=456286560",
            "787385537_456286560",
        ),
        ("https://vk.com/video_ext.php?id=5&oid=-7", "-7_5"),
    ],
)
def test_link_to_the_vk_player_is_a_video(url: str, code: str) -> None:
    """A player link copied from a site that embeds the video."""
    resolution = SourceResolver().resolve(url)

    assert resolution.source == SourceEnum.VK_VIDEO_YDL
    assert resolution.metadata["code"] == code
    assert resolution.url.endswith(f"/video{code}")


@pytest.mark.parametrize(
    "url",
    [
        "https://vk.com/video_ext.php?oid=abc&id=5",
        "https://vk.com/video_ext.php?oid=-7",
        "https://vk.com/video_ext.php",
    ],
)
def test_player_link_without_a_video_is_not_supported(url: str) -> None:
    """A broken player link is refused at once."""
    assert SourceResolver().resolve(url).source != SourceEnum.VK_VIDEO_YDL


def test_ok_player_link_is_a_video() -> None:
    """The link a VK page embeds: ok.ru opens such a video only in the player."""
    resolution = SourceResolver().resolve("https://ok.ru/videoembed/3577707498219")

    assert resolution.source == SourceEnum.OK_YDL
    assert resolution.metadata["code"] == "3577707498219"
    assert resolution.url == "https://ok.ru/videoembed/3577707498219"


def _wall() -> Any:
    controller: Any = VKAPIController.__new__(VKAPIController)
    controller._telegram_id = 1
    controller._proxy = "socks5://ru"
    controller._closed_videos = 0
    controller._left_russia = False
    controller._telegram_bot_controller = MagicMock()
    controller._telegram_bot_controller.send_content_not_found_error = AsyncMock()
    controller._telegram_bot_controller.send_post_without_media = AsyncMock()
    controller.delete_processing_message = AsyncMock()
    controller._send_error_message = AsyncMock()
    controller._cache_dao = MagicMock()
    controller._cache_dao.get_by_filters = AsyncMock(return_value=None)
    controller._switch_to_direct = MagicMock()
    return controller


def _attachment(kind: WallWallpostAttachmentType) -> MagicMock:
    return MagicMock(type=kind)


@pytest.mark.anyio
async def test_post_with_links_only_has_nothing_to_download() -> None:
    """A post with a link and an article is not an «Error»."""
    controller = _wall()
    attachments = [
        _attachment(WallWallpostAttachmentType.LINK),
        _attachment(WallWallpostAttachmentType.ARTICLE),
    ]

    await controller._answer_nothing_to_send(attachments)

    controller._telegram_bot_controller.send_post_without_media.assert_awaited_once()
    controller._send_error_message.assert_not_awaited()


@pytest.mark.anyio
async def test_post_without_attachments_has_nothing_to_download() -> None:
    """A text-only post gets the same answer."""
    controller = _wall()

    await controller._answer_nothing_to_send([])

    controller._telegram_bot_controller.send_post_without_media.assert_awaited_once()


@pytest.mark.anyio
async def test_post_with_a_closed_video_is_private_or_deleted() -> None:
    """The only video of the post is closed: «private or deleted», not «Error»."""
    controller = _wall()
    controller._closed_videos = 1

    await controller._answer_nothing_to_send(
        [_attachment(WallWallpostAttachmentType.VIDEO)],
    )

    send = controller._telegram_bot_controller.send_content_not_found_error
    send.assert_awaited_once()
    controller._send_error_message.assert_not_awaited()


@pytest.mark.anyio
async def test_post_whose_photo_failed_is_still_an_error() -> None:
    """Media was there and the bot lost it: that stays a failure of the bot."""
    controller = _wall()

    await controller._answer_nothing_to_send(
        [_attachment(WallWallpostAttachmentType.PHOTO)],
    )

    controller._send_error_message.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "refusal",
    [
        "ERROR: [vk] Access restricted",
        SIGN_IN,
        FOLLOWERS,
        "ERROR: [vk] This video was removed at the request of the copyright holder",
    ],
)
async def test_closed_video_of_a_post_is_counted(refusal: str) -> None:
    """Every kind of «VK does not give this video» is told from a failure."""
    controller = _wall()
    controller._update_yt_dlp_format = AsyncMock(side_effect=DownloadError(refusal))
    video = MagicMock(owner_id=-1, id=2, access_key="k")

    assert await controller._process_video_attachment(video) is None
    assert controller._closed_videos == 1
    controller._switch_to_direct.assert_not_called()


@pytest.mark.anyio
async def test_video_of_a_post_hidden_from_russia_is_asked_directly() -> None:
    """The same country block as for a video link, once per post."""
    controller = _wall()
    controller._update_yt_dlp_format = AsyncMock(side_effect=DownloadError(BLOCKED))
    video = MagicMock(owner_id=-1, id=2, access_key=None)

    assert await controller._process_video_attachment(video) is None
    controller._switch_to_direct.assert_called_once()
    assert controller._update_yt_dlp_format.await_count == 2
    assert controller._closed_videos == 1


def _bot_controller(send_media_group: AsyncMock) -> TelegramBotController:
    controller = TelegramBotController.__new__(TelegramBotController)
    controller._bot = MagicMock()
    controller._bot.send_media_group = send_media_group
    return controller


def _photos() -> list[PhotoDTO]:
    return [
        PhotoDTO(media_url="https://sun9-50.userapi.com/a.jpg", url="https://vk.ru/w"),
        PhotoDTO(media_url="https://sun9-50.userapi.com/b.jpg", url="https://vk.ru/w"),
    ]


@pytest.mark.anyio
async def test_photos_telegram_could_not_fetch_are_uploaded() -> None:
    """WEBPAGE_CURL_FAILED: the bot downloads the photos and uploads them."""
    refusal = TelegramBadRequest(
        method=MagicMock(),
        message=(
            "Bad Request: failed to send message #1 with the error message"
            ' "WEBPAGE_CURL_FAILED"'
        ),
    )
    send = AsyncMock(side_effect=[refusal, None])
    controller = _bot_controller(send)

    await controller._build_and_send_media_chunk(_photos(), chat_id=1, caption="c")

    assert send.await_count == 2
    first = [item.media for item in send.await_args_list[0].kwargs["media"]]
    second = [item.media for item in send.await_args_list[1].kwargs["media"]]
    assert first == [
        "https://sun9-50.userapi.com/a.jpg",
        "https://sun9-50.userapi.com/b.jpg",
    ]
    assert all(isinstance(item, URLInputFile) for item in second)


@pytest.mark.anyio
async def test_other_refusal_of_a_group_is_not_repeated() -> None:
    """A refusal that is not about fetching a link is reported once."""
    refusal = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: chat not found"
    )
    send = AsyncMock(side_effect=refusal)
    controller = _bot_controller(send)

    with patch("saver_backend.services.telegram.bot_controller.capture_exception"):
        await controller._build_and_send_media_chunk(_photos(), chat_id=1)

    assert send.await_count == 1


@pytest.mark.anyio
async def test_group_sent_at_once_is_not_repeated() -> None:
    """The usual case: one request."""
    send = AsyncMock(return_value=None)
    controller = _bot_controller(send)

    await controller._build_and_send_media_chunk(_photos(), chat_id=1)

    assert send.await_count == 1


async def _run_get_video_info(
    result: dict[str, Any] | Exception,
    target_source: SourceEnum = SourceEnum.YOUTUBE_VIDEO_YDL,
) -> tuple[MagicMock, AsyncMock]:
    bot = MagicMock()
    bot.edit_video_no_formats = AsyncMock()
    bot.edit_video_too_long = AsyncMock()
    bot.edit_failed_video_info = AsyncMock()
    bot.send_choose_quality = AsyncMock(return_value=None)
    bot.delete_message = AsyncMock()

    controller = MagicMock()
    controller.set_user_language = AsyncMock()
    if isinstance(result, Exception):
        controller.get_video_info = AsyncMock(side_effect=result)
    else:
        controller.get_video_info = AsyncMock(return_value=result)
    controller.close = AsyncMock()

    state = MagicMock()
    state.telegram_bot_controller = bot
    state.source_resolver.get_controller.return_value = MagicMock(
        return_value=controller,
    )
    state.source_resolver.resolve.return_value = MagicMock(
        source=target_source,
        url="https://youtu.be/6sFFJ0gefCA",
    )
    kiq = AsyncMock()

    func = getattr(tasks.get_video_info, "original_func", tasks.get_video_info)
    with (
        patch.object(tasks.get_video_info, "kiq", kiq),
        patch.object(tasks, "_record_failure", AsyncMock()),
    ):
        await func(
            resolution=MagicMock(url=VIDEO_URL, source=SourceEnum.VK_VIDEO_YDL),
            telegram_id=1,
            processing_message_id=7,
            state=state,
            db=MagicMock(),
        )
    return bot, kiq


@pytest.mark.anyio
async def test_embedded_video_starts_again_as_its_own_link() -> None:
    """The request goes on under the same «processing» message."""
    bot, kiq = await _run_get_video_info(
        HostedElsewhereError("https://youtu.be/6sFFJ0gefCA"),
    )

    kiq.assert_awaited_once()
    assert kiq.await_args.kwargs["processing_message_id"] == 7
    assert kiq.await_args.kwargs["resolution"].source == SourceEnum.YOUTUBE_VIDEO_YDL
    bot.edit_failed_video_info.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "target_source",
    [SourceEnum.UNSUPPORTED, SourceEnum.VK_VIDEO_YDL],
)
async def test_embedded_video_without_a_source_is_a_failure(
    target_source: SourceEnum,
) -> None:
    """No source for the host, or a loop back to VK: an error, not a new task."""
    bot, kiq = await _run_get_video_info(
        HostedElsewhereError("https://example.com/v/1"),
        target_source=target_source,
    )

    kiq.assert_not_awaited()
    bot.edit_failed_video_info.assert_awaited_once()


@pytest.mark.anyio
async def test_too_long_video_gets_its_own_answer() -> None:
    """«Too long for Telegram» instead of «no formats, try later»."""
    info = {
        "id": "1",
        "title": "stream",
        "duration": 108587,
        "formats": [_format(144, 190), _format(720, 1500)],
    }

    bot, _ = await _run_get_video_info(info)

    bot.edit_video_too_long.assert_awaited_once()
    bot.edit_video_no_formats.assert_not_awaited()


@pytest.mark.anyio
async def test_video_without_formats_keeps_its_answer() -> None:
    """Nothing known about sizes: the old answer stays."""
    bot, _ = await _run_get_video_info({"id": "1", "title": "x", "formats": []})

    bot.edit_video_no_formats.assert_awaited_once()
    bot.edit_video_too_long.assert_not_awaited()
