import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.vk_ydl_source import VkYdlController
from saver_backend.telegram_bot.keyboards.inline import get_vk_quality_keyboard
from saver_backend.telegram_bot.keyboards.callback import VK_QUALITY_PREFIX
from saver_backend.telegram_bot.filters.source import SourceFilter


class VKStates(StatesGroup):
    """States for VK video processing."""
    waiting_for_quality = State()


vk_router = Router()


@vk_router.message(SourceFilter(sources=[SourceEnum.VK]), StateFilter(None))
async def handle_vk_preview(
    message: Message,
    resolution: Resolution,
    state: FSMContext,
) -> None:
    if message.from_user is None:
        return
    
    logging.info(f"[TG] VK Handler called! User {message.from_user.id} sent VK URL: {resolution.url}")
    logging.info(f"[TG] Resolution source: {resolution.source}, metadata: {resolution.metadata}")
    
    try:
        vk_controller = VkYdlController(
            resolution=resolution,
            telegram_bot_controller=None, 
            telegram_id=message.from_user.id,
        )

        video_info = await vk_controller.get_video_info(resolution.url)
        if not video_info:
            await message.answer("Не удалось получить информацию о видео")
            return
    
        available_formats = await vk_controller.get_available_formats(resolution.url)
        if not available_formats:
            await message.answer("Не удалось получить доступные форматы видео.")
            return
        
        await state.update_data(
            resolution=resolution,
            video_info=video_info,
            available_formats=available_formats
        )

        title = video_info.get('title', 'Без названия')
        duration = video_info.get('duration', 0)
        duration_text = f"{duration // 60}:{duration % 60:02d}" if duration else "Неизвестно"
        
        caption = f"<b>{title}</b>\nДлительность: {duration_text}\n\nВыберите качество видео:"

        if video_info.get('thumbnail'):
            await message.answer_photo(
                photo=video_info['thumbnail'],
                caption=caption,
                reply_markup=get_vk_quality_keyboard(available_formats),
                parse_mode="HTML"
            )
        else:
            await message.answer(
                text=caption,
                reply_markup=get_vk_quality_keyboard(available_formats),
                parse_mode="HTML"
            )
        
        await state.set_state(VKStates.waiting_for_quality)
        
    except Exception as e:
        logging.error(f"Error handling VK preview: {e}", exc_info=True)
        await message.answer(f"Произошла ошибка при обработке видео: {str(e)}")


@vk_router.callback_query(
    F.data.startswith(VK_QUALITY_PREFIX),
    VKStates.waiting_for_quality
)
async def handle_vk_quality_selection(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Handle vk quality selection and start download
    """
    if not isinstance(callback.message, Message) or callback.from_user is None:
        return
    
    try:


        data = await state.get_data()
        resolution: Resolution = data.get('resolution')
        video_info = data.get('video_info')
        available_formats = data.get('available_formats')
        
        if not resolution or not video_info:
            await callback.answer("Данные о видео потеряны. Попробуйте снова.")
            await state.clear()
            return
        
        quality = callback.data.replace(VK_QUALITY_PREFIX, "")
        
        # Обновляем resolution с выбранным качеством
        resolution.metadata["quality"] = quality
        
        await callback.answer(f"Начинаю скачивание в качестве {quality}...")

        await callback.message.delete()

        vk_controller = VkYdlController(
            resolution=resolution,
            telegram_bot_controller=None,  # Упрощенный режим
            telegram_id=callback.from_user.id,
        )
        
        # Отправляем сообщение о начале скачивания
        progress_message = await callback.message.answer(
            f"Скачиваю видео в качестве {quality}...\nПожалуйста, подождите"
        )
        
        try:
            # Скачиваем видео напрямую
            await vk_controller.download_video()
            
            # Получаем путь к скачанному файлу
            downloaded_file = vk_controller._get_downloaded_file()
            logging.info(f"Downloaded file path: {downloaded_file}")
            logging.info(f"File exists: {downloaded_file.exists() if downloaded_file else False}")
            
            if downloaded_file and downloaded_file.exists():
                # Отправляем видео пользователю
                from aiogram.types import FSInputFile
                
                await progress_message.edit_text("Отправляю видео...")
                
                # Получаем информацию о видео для caption
                video_info = data.get('video_info', {})
                title = video_info.get('title', 'VK видео')
                duration = video_info.get('duration', 0)
                duration_text = f"{duration // 60}:{duration % 60:02d}" if duration else "Неизвестно"
                
                caption = f"🎬 <b>{title}</b>\n⏱ Длительность: {duration_text}\n📹 Качество: {quality}"
                
                # Отправляем видео
                await callback.message.answer_video(
                    video=FSInputFile(path=downloaded_file),
                    caption=caption,
                    parse_mode="HTML"
                )
                
                # Удаляем временный файл
                downloaded_file.unlink()
                logging.info(f"Deleted temporary file: {downloaded_file}")
                
                # Удаляем сообщение о прогрессе
                await progress_message.delete()
                
            else:
                # Попробуем найти файл в директории скачивания
                download_dir = vk_controller._download_directory
                logging.info(f"Searching for files in: {download_dir}")
                
                if download_dir.exists():
                    video_files = list(download_dir.glob("*.mp4")) + list(download_dir.glob("*.webm"))
                    logging.info(f"Found video files: {video_files}")
                    
                    if video_files:
                        # Берем самый новый файл
                        downloaded_file = max(video_files, key=lambda f: f.stat().st_mtime)
                        logging.info(f"Using newest file: {downloaded_file}")
                        
                        # Отправляем видео
                        from aiogram.types import FSInputFile
                        
                        await progress_message.edit_text("Отправляю видео....")
                        
                        # Получаем информацию о видео для caption
                        video_info = data.get('video_info', {})
                        title = video_info.get('title', 'VK видео')
                        duration = video_info.get('duration', 0)
                        duration_text = f"{duration // 60}:{duration % 60:02d}" if duration else "Неизвестно"
                        
                        caption = f"🎬 <b>{title}</b>\n⏱ Длительность: {duration_text}\n📹 Качество: {quality}"
                        
                        # Отправляем видео
                        await callback.message.answer_video(
                            video=FSInputFile(path=downloaded_file),
                            caption=caption,
                            parse_mode="HTML"
                        )
                        
                        # Удаляем временный файл
                        downloaded_file.unlink()
                        logging.info(f"Deleted temporary file: {downloaded_file}")
                        
                        # Удаляем сообщение о прогрессе
                        await progress_message.delete()
                    else:
                        await progress_message.edit_text("Файл не найден после скачивания.")
                else:
                    await progress_message.edit_text("Директория скачивания не найдена.")
            
        except Exception as e:
            logging.error(f"Error downloading VK video: {e}", exc_info=True)
            await progress_message.edit_text(f"Ошибка при скачивании видео: {str(e)}")
        
        # Очищаем состояние
        await state.clear()
        
    except Exception as e:
        logging.error(f"Error handling VK quality selection: {e}")
        await callback.answer("Произошла ошибка при скачивании. Попробуйте позже.")
        await state.clear()


@vk_router.message(SourceFilter(sources=[SourceEnum.VK]))
async def handle_vk_without_state(
    message: Message,
    resolution: Resolution,
    state: FSMContext,
) -> None:
    """
    Handle VK video without state filter - fallback handler.

    :param message: Message.
    :param resolution: Resolution.
    :param state: FSM state.
    """
    if message.from_user is None:
        return
    
    logging.info(f"[TG] VK Handler (no state) called! User {message.from_user.id} sent VK URL: {resolution.url}")
    logging.info(f"[TG] Current state: {await state.get_state()}")
    
    # Очищаем состояние и вызываем основной handler
    await state.clear()
    await handle_vk_preview(message, resolution, state)


@vk_router.callback_query(
    F.data.startswith(VK_QUALITY_PREFIX),
    StateFilter(None)
)
async def handle_vk_quality_out_of_state(callback: CallbackQuery) -> None:
    """
    Handle VK quality selection when not in waiting state.

    :param callback: Callback query.
    """
    await callback.answer("Сессия истекла. Отправьте ссылку заново.")
