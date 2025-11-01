from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from saver_backend.services.i18n import gettext as _
from saver_backend.services.telegram.utils import check_subscriptions
from saver_backend.telegram_bot.keyboards.callback import CHECK_SUBSCRIPTIONS
from saver_backend.telegram_bot.keyboards.inline import get_subscribe_keyboard

start_router = Router()


@start_router.message(CommandStart())
async def on_start(
    message: Message,
    bot: Bot,
) -> None:
    """
    Handle start command.

    :param message: message object.
    :param bot: Bot.
    :param user_dao: User DAO.
    """
    if message.from_user is None:
        return

    username = (await bot.me()).username
    if username is None:
        return

    await message.answer(
        text=_("welcome message"),
        reply_markup=get_subscribe_keyboard(),
    )


@start_router.callback_query(F.data == CHECK_SUBSCRIPTIONS)
async def on_check_subscriptions(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    """
    Handle check subscriptions callback.

    :param callback: callback query.
    :param bot: Bot.
    """
    if not isinstance(callback.message, Message):
        return

    is_subscribed = await check_subscriptions(
        bot=bot,
        telegram_id=callback.from_user.id,
    )
    if is_subscribed:
        await callback.message.edit_text(
            text=_("information message"),
        )
    else:
        await callback.answer(
            text=_("not subscribed to channels"),
        )
