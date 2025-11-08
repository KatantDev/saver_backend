import logging

from taskiq import TaskiqDepends

from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.exceptions import (
    TikTokYtDlpDownloaderError,
)
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.task_manager.state import DatabaseState, SaverState
from saver_backend.tkq import broker


@broker.task()
async def save_video(
    resolution: Resolution,
    telegram_id: int,
    format_id: str | None = None,
    state: SaverState = TaskiqDepends(),
    db: DatabaseState = TaskiqDepends(),
) -> None:
    """
    Save video.

    :param resolution: Resolution of the video.
    :param telegram_id: Telegram ID of the user.
    :param format_id: Format ID of the video.
    :param state: Saver state.
    :param db: Database state with DAOs.
    """
    # Достаем контроллер на основе резолюшена
    logging.info("Resolving controller for %s", resolution)
    yt_dlp_controller = state.source_resolver.get_controller(resolution)
    if yt_dlp_controller is None:
        return

    # Отправляем сообщение о том, что начинаем загрузку
    message_id = await state.telegram_bot_controller.send_start_downloading(
        telegram_id=telegram_id,
        percent=0,
    )

    # Инициализируем контроллер + ставим язык
    controller = yt_dlp_controller(
        resolution=resolution,
        telegram_bot_controller=state.telegram_bot_controller,
        telegram_id=telegram_id,
        video_cache_dao=db.video_cache_dao,
        user_dao=db.user_dao,
        message_id=message_id,
        format_id=format_id,
    )
    await controller.set_user_language()

    # Качаем видос
    try:
        await controller.download_video()
    except TikTokYtDlpDownloaderError:
        await state.telegram_bot_controller.send_tiktok_error_downloading(
            telegram_id=telegram_id,
            language="en",
        )
    except Exception as error:
        logging.exception(error)
    finally:
        await controller.close()


@broker.task(
    result_timeout=60,
    time_limit=120,
)
async def process_inline_query(
    resolution: Resolution,
    telegram_id: int,
    inline_query_id: str,
    state: SaverState = TaskiqDepends(),
    db: DatabaseState = TaskiqDepends(),
) -> None:
    """
    Process an inline query to download a video and return the result.

    :param resolution: The resolved URL information.
    :param telegram_id: The ID of the user who sent the query.
    :param inline_query_id: The ID of the inline query to answer.
    :param state: The application state.
    :param db: The database state.
    """
    controller_class = state.source_resolver.get_controller(resolution)
    if not controller_class:
        return

    controller = controller_class(
        resolution=resolution,
        telegram_bot_controller=state.telegram_bot_controller,
        telegram_id=telegram_id,
        video_cache_dao=db.video_cache_dao,
        user_dao=db.user_dao,
        inline_query_id=inline_query_id,
    )
    await controller.set_user_language()
    try:
        await controller.download_video()
    except Exception as error:
        logging.exception(error)
    finally:
        await controller.close()


@broker.task()
async def get_video_info(
    resolution: Resolution,
    telegram_id: int,
    processing_message_id: int,
    state: SaverState = TaskiqDepends(),
    db: DatabaseState = TaskiqDepends(),
) -> None:
    """
    Fetches video info in the background and sends format selection to the user.

    :param resolution: Resolution object.
    :param telegram_id: The user's Telegram ID.
    :param processing_message_id: The ID of the "processing" message to edit/delete.
    :param state: The application state.
    :param db: The database state.
    """
    # Достаем контроллер на основе резолюшена
    controller_class = state.source_resolver.get_controller(resolution)
    if not controller_class:
        logging.error("Not found controller for %s", resolution)
        return

    # Инициализируем контроллер + ставим язык
    controller = controller_class(
        resolution=resolution,
        telegram_bot_controller=state.telegram_bot_controller,
        telegram_id=telegram_id,
        video_cache_dao=db.video_cache_dao,
        user_dao=db.user_dao,
    )
    await controller.set_user_language()

    # Пытаемся получить информацию по видосу
    try:
        info_dict = await controller.get_video_info(url=resolution.url)
    except Exception as error:
        logging.exception(error)
        info_dict = None
    finally:
        await controller.close()

    if not info_dict:
        await state.telegram_bot_controller.edit_failed_video_info(
            telegram_id=telegram_id,
            message_id=processing_message_id,
        )
        return

    # Формируем информацию по видосу
    video_dto = VideoDTO.from_yt_dlp(info=info_dict)
    if not video_dto.formats:
        await state.telegram_bot_controller.edit_video_no_formats(
            telegram_id=telegram_id,
            message_id=processing_message_id,
        )
        return

    # Складываем данные в машину состояний + отправляем сообщение с удалением старого
    await state.telegram_bot_controller.set_fsm_data(
        user_id=telegram_id,
        chat_id=telegram_id,
        data={
            "video_dto": video_dto.model_dump(mode="json"),
            "resolution": resolution.model_dump(mode="json"),
        },
    )
    await state.telegram_bot_controller.send_choose_quality(
        telegram_id=telegram_id,
        video_dto=video_dto,
    )
    await state.telegram_bot_controller.delete_message(
        telegram_id=telegram_id,
        message_id=processing_message_id,
    )
