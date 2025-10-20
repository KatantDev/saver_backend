import logging
from pathlib import Path

from taskiq import TaskiqDepends
from aiogram.types import FSInputFile

from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.exceptions import TikTokYtDlpDownloaderError
from saver_backend.services.downloaders.vk_ydl_source import VkYdlController
from saver_backend.services.i18n import gettext as _
from saver_backend.task_manager.state import SaverState
from saver_backend.tkq import broker


@broker.task()
async def save_video(
    resolution: Resolution,
    telegram_id: int,
    state: SaverState = TaskiqDepends(),
) -> None:
    """
    Save video.

    :param resolution: Resolution of the video.
    :param telegram_id: Telegram ID of the user.
    :param state: Saver state.
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
    )
    try:
        await controller.download_video()
    except TikTokYtDlpDownloaderError:
        await state.telegram_bot_controller.send_tiktok_error_downloading(
            telegram_id=telegram_id,
        )


@broker.task()
async def save_vk_video(
    resolution: Resolution,
    telegram_id: int,
    quality: str,
    video_info: dict,
    state: SaverState = TaskiqDepends(),
) -> None:
    """
    Save VK video with selected quality.

    :param resolution: Resolution of the video.
    :param telegram_id: Telegram ID of the user.
    :param quality: Selected video quality.
    :param video_info: Video information dictionary.
    :param state: Saver state.
    """
    logging.info(f"Starting VK video download for user {telegram_id} in quality {quality}")
    
    progress_message_id = await state.telegram_bot_controller.bot.send_message(
        chat_id=telegram_id,
        text=_("vk downloading").format(quality=quality),
    )
    progress_message_id = progress_message_id.message_id
    
    try:
        resolution.metadata["quality"] = quality

        vk_controller = VkYdlController(
            resolution=resolution,
            telegram_bot_controller=None, 
            telegram_id=telegram_id,
        )

        await vk_controller.download_video()

        downloaded_file = vk_controller._get_downloaded_file()
        logging.info(f"Downloaded file path: {downloaded_file}")
        logging.info(f"File exists: {downloaded_file.exists() if downloaded_file else False}")
        
        if downloaded_file and downloaded_file.exists():
            await state.telegram_bot_controller.bot.edit_message_text(
                message_id=progress_message_id,
                chat_id=telegram_id,
                text=_("vk sending video"),
            )

            title = video_info.get('title', 'VK видео')
            duration = video_info.get('duration', 0)
            duration_text = f"{duration // 60}:{duration % 60:02d}" if duration else "Неизвестно"
            
            caption = _("vk video caption").format(
                title=title,
                duration=duration_text,
                quality=quality,
            )

            await state.telegram_bot_controller.bot.send_video(
                chat_id=telegram_id,
                video=FSInputFile(path=downloaded_file),
                caption=caption,
                parse_mode="HTML",
            )
            
            downloaded_file.unlink()
            logging.info(f"Deleted temporary file: {downloaded_file}")
            

            await state.telegram_bot_controller.bot.delete_message(
                message_id=progress_message_id,
                chat_id=telegram_id,
            )
            
        else:

            download_dir = vk_controller._download_directory
            logging.info(f"Searching for files in: {download_dir}")
            
            if download_dir.exists():
                video_files = list(download_dir.glob("*.mp4")) + list(download_dir.glob("*.webm"))
                logging.info(f"Found video files: {video_files}")
                
                if video_files:

                    downloaded_file = max(video_files, key=lambda f: f.stat().st_mtime)
                    logging.info(f"Using newest file: {downloaded_file}")

                    await state.telegram_bot_controller.bot.edit_message_text(
                        message_id=progress_message_id,
                        chat_id=telegram_id,
                        text=_("vk sending video"),
                    )
                    
                    title = video_info.get('title', 'VK видео')
                    duration = video_info.get('duration', 0)
                    duration_text = f"{duration // 60}:{duration % 60:02d}" if duration else "Неизвестно"
                    
                    caption = _("vk video caption").format(
                        title=title,
                        duration=duration_text,
                        quality=quality,
                    )

                    await state.telegram_bot_controller.bot.send_video(
                        chat_id=telegram_id,
                        video=FSInputFile(path=downloaded_file),
                        caption=caption,
                        parse_mode="HTML",
                    )

                    downloaded_file.unlink()
                    logging.info(f"Deleted temporary file: {downloaded_file}")
                    
                    await state.telegram_bot_controller.bot.delete_message(
                        message_id=progress_message_id,
                        chat_id=telegram_id,
                    )
                else:
                    await state.telegram_bot_controller.bot.edit_message_text(
                        message_id=progress_message_id,
                        chat_id=telegram_id,
                        text=_("vk file not found"),
                    )
            else:
                await state.telegram_bot_controller.bot.edit_message_text(
                    message_id=progress_message_id,
                    chat_id=telegram_id,
                    text=_("vk directory not found"),
                )
        
    except Exception as e:
        logging.error(f"Error downloading VK video: {e}", exc_info=True)
        try:
            await state.telegram_bot_controller.bot.edit_message_text(
                message_id=progress_message_id,
                chat_id=telegram_id,
                text=_("vk download error").format(error=str(e)),
            )
        except Exception:
            pass
