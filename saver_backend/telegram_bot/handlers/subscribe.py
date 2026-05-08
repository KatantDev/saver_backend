from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from saver_backend.services.i18n import gettext as _
from saver_backend.telegram_bot.filters.subscribe import SubscribeFilter
from saver_backend.telegram_bot.keyboards.inline import get_subscribe_keyboard

subscribe_router = Router()


@subscribe_router.message(SubscribeFilter())
async def on_not_subscribed_message(message: Message) -> None:
    """
    Handle not subscribed message.

    :param message: Message.
    """
    await message.answer(
        text=_("left from channels"),
        reply_markup=get_subscribe_keyboard(),
    )


@subscribe_router.callback_query(SubscribeFilter(), F.chat.type == "private")
async def on_not_subscribed_callback(query: CallbackQuery) -> None:
    """
    Handle subscribed message.

    :param query: CallbackQuery.
    """
    if not isinstance(query.message, Message):
        return

    await query.message.delete()
    await query.message.answer(
        text=_("information message"),
    )
