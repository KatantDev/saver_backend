import logging

from taskiq import TaskiqDepends

from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.exceptions import TikTokYtDlpDownloaderError
from saver_backend.task_manager.state import DatabaseState, SaverState
from saver_backend.tkq import broker


@broker.task()
async def save_video(
    resolution: Resolution,
    telegram_id: int,
    state: SaverState = TaskiqDepends(),
    db: DatabaseState = TaskiqDepends(),
) -> None:
    """
    Save video.

    :param resolution: Resolution of the video.
    :param telegram_id: Telegram ID of the user.
    :param state: Saver state.
    :param db: Database state with DAOs.
    """
    logging.info("Resolving controller for %s", resolution)
    yt_dlp_controller = state.source_resolver.get_controller(resolution)
    if yt_dlp_controller is None:
        return

    message_id = await state.telegram_bot_controller.send_start_downloading(
        telegram_id=telegram_id,
        percent=0,
    )

    controller = yt_dlp_controller(
        resolution=resolution,
        telegram_bot_controller=state.telegram_bot_controller,
        telegram_id=telegram_id,
        message_id=message_id,
        video_cache_dao=db.video_cache_dao,
    )
    try:
        await controller.download_video()
    except TikTokYtDlpDownloaderError:
        await state.telegram_bot_controller.send_tiktok_error_downloading(
            telegram_id=telegram_id,
        )
