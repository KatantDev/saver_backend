import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.vk_ydl_source import VkYdlController
from saver_backend.services.i18n import gettext as _
from saver_backend.task_manager.tasks import save_vk_video
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
            await message.answer(_("vk video info failed"))
            return
    
        available_formats = await vk_controller.get_available_formats(resolution.url)
        if not available_formats:
            await message.answer(_("vk formats failed"))
            return
        
        await state.update_data(
            resolution=resolution,
            video_info=video_info,
            available_formats=available_formats
        )

        title = video_info.get('title', 'Без названия')
        duration = video_info.get('duration', 0)
        duration_text = f"{duration // 60}:{duration % 60:02d}" if duration else "Неизвестно"
        
        caption = f"<b>{title}</b>\nДлительность: {duration_text}\n\n{_('vk select quality')}"

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
        await message.answer(_("vk processing error").format(error=str(e)))


@vk_router.callback_query(
    F.data.startswith(VK_QUALITY_PREFIX),
    VKStates.waiting_for_quality
)
async def handle_vk_quality_selection(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Хендлер с выбором качества vk   
    """
    if not isinstance(callback.message, Message) or callback.from_user is None:
        return
    
    try:
        data = await state.get_data()
        resolution: Resolution = data.get('resolution')
        video_info = data.get('video_info')
        available_formats = data.get('available_formats')
        
        if not resolution or not video_info:
            await callback.answer(_("vk video data lost"))
            await state.clear()
            return
        
        quality = callback.data.replace(VK_QUALITY_PREFIX, "")
        
        await callback.answer(_("vk starting download").format(quality=quality))
        await callback.message.delete()

        await save_vk_video.kiq(
            resolution=resolution,
            telegram_id=callback.from_user.id,
            quality=quality,
            video_info=video_info,
        )

        await state.clear()
        
    except Exception as e:
        logging.error(f"Error handling VK quality selection: {e}")
        await callback.answer(_("vk download error generic"))
        await state.clear()


@vk_router.message(SourceFilter(sources=[SourceEnum.VK]))
async def handle_vk_without_state(
    message: Message,
    resolution: Resolution,
    state: FSMContext,
) -> None:
    if message.from_user is None:
        return
    
    logging.info(f"[TG] VK Handler (no state) called! User {message.from_user.id} sent VK URL: {resolution.url}")
    logging.info(f"[TG] Current state: {await state.get_state()}")
    
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
    await callback.answer(_("vk session expired"))
