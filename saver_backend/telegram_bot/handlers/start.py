import logging
from aiogram import Bot, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
# from aiogram.types import User

# from saver_backend.db.dao.user_dao import UserDAO
# from saver_backend.services.i18n import gettext as _
# from saver_backend.services.telegram.utils import check_subscriptions
# from saver_backend.telegram_bot.keyboards.callback import CHECK_SUBSCRIPTIONS
# from saver_backend.telegram_bot.keyboards.inline import get_subscribe_keyboard

start_router = Router()

# async def create_or_update_user(
#     user_dao: UserDAO,
#     tg_user: User,
# ) -> None:
#     """
#     Create or update user.

#     :param user_dao: User DAO.
#     :param tg_user: Telegram user.
#     """
#     user = await user_dao.get_by_id(telegram_id=tg_user.id)
#     if user is None:
#         await user_dao.create(
#             telegram_id=tg_user.id,
#             username=tg_user.username,
#             first_name=tg_user.first_name,
#             last_name=tg_user.last_name,
#             language_code=tg_user.language_code,
#         )
#     else:
#         await user_dao.update(
#             telegram_id=tg_user.id,
#             username=tg_user.username,
#             first_name=tg_user.first_name,
#             last_name=tg_user.last_name,
#             language_code=tg_user.language_code,
#         )


@start_router.message(CommandStart())
async def on_start(
    message: Message,
    bot: Bot,
    # user_dao: UserDAO,
) -> None:
    """
    Handle start command.

    :param message: message object.
    :param bot: Bot.
    # :param user_dao: User DAO.
    """
    if message.from_user is None:
        return
    logging.info(f"[TG] User {message.from_user.id} started bot. Text: {message.text}")
    # await create_or_update_user(user_dao=user_dao, tg_user=message.from_user)

    username = (await bot.me()).username
    if username is None:
        return

    await message.answer(
        #text=_("welcome message"),
        #reply_markup=get_subscribe_keyboard(),
        text="hi",
        # reply_markup=get_subscribe_keyboard(),
    )


# @start_router.callback_query(F.data == CHECK_SUBSCRIPTIONS)
# async def on_check_subscriptions(
#     callback: CallbackQuery,
#     bot: Bot,
# ) -> None:
#     """
#     Handle check subscriptions callback.

#     :param callback: callback query.
#     :param bot: Bot.
#     """
#     if not isinstance(callback.message, Message):
#         return

#     is_subscribed = await check_subscriptions(
#         bot=bot,
#         telegram_id=callback.from_user.id,
#     )
#     if is_subscribed:
#         await callback.message.edit_text(
#             text=_("information message"),
#         )
#     else:
#         await callback.answer(
#             text=_("not subscribed to channels"),
#         )
