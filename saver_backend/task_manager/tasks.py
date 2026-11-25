import asyncio
import html
import json
import logging
from typing import Any

from taskiq import TaskiqDepends

from saver_backend.entities.enums import ContentTypeEnum, SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services import failure_log
from saver_backend.services.cleanup.clear_old import CleanupService
from saver_backend.services.downloaders.exceptions import (
    HostedElsewhereError,
    TikTokYtDlpDownloaderError,
)
from saver_backend.services.downloaders.instagram_session import (
    InstagramSessionError,
    refresh_instagram_session,
)
from saver_backend.services.downloaders.schema import (
    VideoDTO,
    every_quality_is_oversized,
    is_live_error,
    is_live_stream,
)
from saver_backend.task_manager.state import DatabaseState, SaverState
from saver_backend.tkq import broker

# Sources with a quality menu that take over a video another site only embeds
HANDED_OVER_SOURCES = frozenset({SourceEnum.YOUTUBE_VIDEO_YDL, SourceEnum.OK_YDL})


async def _record_failure(
    db: DatabaseState,
    mark: failure_log.FailureMark,
    resolution: Resolution,
    telegram_id: int,
    stage: str,
) -> None:
    """
    Keep the full link of a request the user got no result for.

    Writing the record never breaks the request: a failure here is only logged.

    :param db: Database state.
    :param mark: What happened while the request was handled.
    :param resolution: The link and its source.
    :param telegram_id: Telegram ID of the user.
    :param stage: video_info, download or inline.
    """
    reason = failure_log.outcome(mark)
    if reason is None:
        return
    try:
        await db.session.rollback()
        await db.failed_request_dao.create(
            telegram_id=telegram_id,
            source=resolution.source,
            host=failure_log.host_of(resolution.url),
            url=resolution.url,
            stage=stage,
            reason=reason,
            error=mark.error,
            traceback=mark.traceback,
        )
    except Exception as error:
        logging.warning("Could not record failed request %s: %s", resolution.url, error)


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
    # Getting controller for the resolution
    db.session.tolog = (  # type: ignore[attr-defined]
        f"{resolution.url}; user_id={telegram_id}"
    )
    logging.info("Resolving controller for %s, user_id=%s", resolution, telegram_id)
    yt_dlp_controller = state.source_resolver.get_controller(resolution)
    if yt_dlp_controller is None:
        return

    # Sending start downloading message
    message_id = await state.telegram_bot_controller.send_start_downloading(
        telegram_id=telegram_id,
        percent=0,
    )

    # Initializing controller + setting user language
    controller = yt_dlp_controller(
        resolution=resolution,
        telegram_bot_controller=state.telegram_bot_controller,
        telegram_id=telegram_id,
        user_dao=db.user_dao,
        history_dao=db.history_dao,
        cache_dao=db.cache_dao,
        message_id=message_id,
        format_id=format_id,
    )
    await controller.set_user_language()
    mark = failure_log.start()

    # Downloading video with error handling
    try:
        await controller.download_video()
    except TikTokYtDlpDownloaderError:
        await controller.delete_processing_message()
        await state.telegram_bot_controller.send_tiktok_error_downloading(
            telegram_id=telegram_id,
            language="en",
        )
    except Exception as error:
        logging.exception(f"{resolution.url}; user_id={telegram_id} {error}")
        if controller.message_id is not None:
            await controller.delete_processing_message()
            await state.telegram_bot_controller.send_error_downloading(
                telegram_id=telegram_id,
                resolution_url=resolution.url,
            )
    finally:
        await controller.close()
        await _record_failure(db, mark, resolution, telegram_id, "download")


# Telegram refuses an inline answer that comes later than about ten seconds
# after the query; before that the user is told to repeat the query. By the
# prod log, answers were accepted up to the ninth second, so 7 seconds turned
# away requests that would have made it (32 a day).
INLINE_ANSWER_DEADLINE = 8.5


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
    db.session.tolog = (  # type: ignore[attr-defined]
        f"{resolution.url}; user_id={telegram_id}"
    )
    controller_class = state.source_resolver.get_controller(resolution)
    if not controller_class:
        return
    logging.info(
        "[inline] Getting controller for %s; user_id=%s",
        resolution,
        telegram_id,
    )
    controller = controller_class(
        resolution=resolution,
        telegram_bot_controller=state.telegram_bot_controller,
        telegram_id=telegram_id,
        user_dao=db.user_dao,
        history_dao=db.history_dao,
        cache_dao=db.cache_dao,
        inline_query_id=inline_query_id,
    )
    await controller.set_user_language()
    mark = failure_log.start()
    download = asyncio.create_task(controller.download_video())
    try:
        finished, _pending = await asyncio.wait(
            {download},
            timeout=INLINE_ANSWER_DEADLINE,
        )
        if not finished:
            logging.warning(
                "[inline] %s is not ready in %s s, the user is asked to repeat",
                resolution.url,
                INLINE_ANSWER_DEADLINE,
            )
            await state.telegram_bot_controller.answer_inline_query_preparing(
                inline_query_id,
            )
        await download
    except Exception as error:
        logging.exception(error)
    finally:
        if not download.done():
            download.cancel()
        await controller.close()
        await _record_failure(db, mark, resolution, telegram_id, "inline")


async def _hand_over(
    state: SaverState,
    url: str,
    resolution: Resolution,
    telegram_id: int,
    processing_message_id: int,
) -> bool:
    """
    Pass a video VK only embeds to the source of the site that hosts it.

    The request goes on with the same «processing» message, as if the user
    had sent that link.

    :param state: The application state.
    :param url: Link to the video on its own site.
    :param resolution: What the user sent.
    :param telegram_id: The user's Telegram ID.
    :param processing_message_id: The ID of the "processing" message.
    :return: False when the bot has no source for that site.
    """
    target = state.source_resolver.resolve(url)
    if target.source not in HANDED_OVER_SOURCES or target.source == resolution.source:
        logging.error("No source for the embedded video %s", url)
        return False
    logging.info(
        "%s is hosted elsewhere, handing over to %s; user_id=%s",
        resolution.url,
        target.url,
        telegram_id,
    )
    await get_video_info.kiq(
        resolution=target,
        telegram_id=telegram_id,
        processing_message_id=processing_message_id,
    )
    return True


async def _answer_no_formats(
    state: SaverState,
    info_dict: dict[str, Any],
    resolution: Resolution,
    telegram_id: int,
    processing_message_id: int,
) -> None:
    """
    Say why the quality menu is empty: too long for Telegram, or no formats.

    :param state: The application state.
    :param info_dict: The dictionary from yt_dlp.extract_info.
    :param resolution: What the user sent.
    :param telegram_id: The user's Telegram ID.
    :param processing_message_id: The ID of the "processing" message.
    """
    if every_quality_is_oversized(info_dict):
        logging.info(
            "Every quality of %s is over Telegram's limit; user_id=%s",
            resolution.url,
            telegram_id,
        )
        await state.telegram_bot_controller.edit_video_too_long(
            telegram_id=telegram_id,
            message_id=processing_message_id,
        )
        return
    await state.telegram_bot_controller.edit_video_no_formats(
        telegram_id=telegram_id,
        message_id=processing_message_id,
    )


@broker.task()
async def get_video_info(  # noqa: PLR0911
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
    db.session.tolog = (  # type: ignore[attr-defined]
        f"{resolution.url}; user_id={telegram_id}"
    )
    # Getting controller for the resolution
    controller_class = state.source_resolver.get_controller(resolution)
    if not controller_class:
        logging.error(
            "Not found controller for %s, user_id=%s", resolution, telegram_id
        )
        return

    # Initializing controller + setting user language
    logging.info(
        "Getting controller for %s; user_id=%s",
        resolution,
        telegram_id,
    )
    controller = controller_class(
        resolution=resolution,
        telegram_bot_controller=state.telegram_bot_controller,
        telegram_id=telegram_id,
        user_dao=db.user_dao,
        history_dao=db.history_dao,
        cache_dao=db.cache_dao,
        message_id=processing_message_id,
    )
    await controller.set_user_language()
    mark = failure_log.start()
    try:
        # Trying to get video info
        try:
            info_dict = await controller.get_video_info(url=resolution.url)
        except HostedElsewhereError as moved:
            if await _hand_over(
                state, moved.url, resolution, telegram_id, processing_message_id
            ):
                return
            info_dict = None
        except Exception as error:
            if is_live_error(error):
                logging.info("Scheduled stream refused: %s; %s", resolution.url, error)
                await state.telegram_bot_controller.edit_video_is_live(
                    telegram_id=telegram_id,
                    message_id=processing_message_id,
                )
                return
            logging.exception(error)
            info_dict = None
        finally:
            await controller.close()

        if not info_dict:
            await state.telegram_bot_controller.edit_failed_video_info(
                telegram_id=telegram_id,
                message_id=controller.message_id,
            )
            return

        if is_live_stream(info_dict):
            logging.info(
                "Live stream refused: %s; user_id=%s", resolution.url, telegram_id
            )
            await state.telegram_bot_controller.edit_video_is_live(
                telegram_id=telegram_id,
                message_id=processing_message_id,
            )
            return

        # Creating data transfer object from info dict
        video_dto = VideoDTO.from_yt_dlp(info=info_dict)
        if not video_dto.formats:
            await _answer_no_formats(
                state, info_dict, resolution, telegram_id, processing_message_id
            )
            return

        # Sending quality selection message and deleting processing message
        quality_selection_message = (
            await state.telegram_bot_controller.send_choose_quality(
                telegram_id=telegram_id,
                video_dto=video_dto,
                contenttype=getattr(controller, "contenttype", ContentTypeEnum.VIDEO),
            )
        )
        await state.telegram_bot_controller.delete_message(
            telegram_id=telegram_id,
            message_id=processing_message_id,
        )

        if not quality_selection_message:
            logging.error(
                "Failed to send quality selection message for user %s",
                telegram_id,
            )
            return

        # Put data into FSM for further processing
        await state.telegram_bot_controller.set_fsm_data(
            user_id=telegram_id,
            chat_id=telegram_id,
            data={
                "video_dto": video_dto.model_dump(mode="json"),
                "videotheatre_dto": json.dumps(info_dict.get("videotheatre_dto", {})),
                "resolution": resolution.model_dump(mode="json"),
                "quality_selection_message_id": quality_selection_message.message_id,
            },
        )
    finally:
        await _record_failure(db, mark, resolution, telegram_id, "video_info")


@broker.task(
    schedule=[{"cron": "*/10 * * * *"}],  # Every 10 minutes
)
async def cleanup_old_files_task(
    state: SaverState = TaskiqDepends(),
) -> None:
    """
    Periodic task to clean up old downloaded files.

    Runs every 10 minutes and removes files older than 1 hour.
    """
    logging.info("Running scheduled cleanup task")

    service = CleanupService()
    await service.cleanup()


@broker.task(
    schedule=[{"cron": "15 */6 * * *"}],  # Every 6 hours
)
async def refresh_instagram_session_task(
    state: SaverState = TaskiqDepends(),
) -> None:
    """
    Periodic task to keep the Instagram service account logged in.

    Also started when an Instagram download fails because login is required.
    Admins are notified when the session cannot be restored automatically.
    """
    try:
        await refresh_instagram_session()
    except InstagramSessionError as error:
        logging.error("Instagram session refresh failed: %s", error)
        await state.telegram_bot_controller.send_admin_message(
            f"Instagram session needs attention: {html.escape(str(error))}",
        )
    except Exception:
        logging.exception("Instagram session refresh failed")
