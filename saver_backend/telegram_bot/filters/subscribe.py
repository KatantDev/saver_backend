from aiogram import Bot
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message

from saver_backend.services.telegram.utils import check_subscriptions


# class SubscribeFilter(Filter):
#     """Filter and check subscriptions for our channels."""

#     async def __call__(self, event: Message | CallbackQuery, bot: Bot) -> bool:
#         """
#         Check subscriptions for our channels.

#         :param event: Message or CallbackQuery.
#         :param bot: Bot.
#         :return: True if subscribed, False otherwise.
#         """
#         if event.from_user is None:
#             return False

#         is_subscribed = await check_subscriptions(
#             bot=bot,
#             telegram_id=event.from_user.id,
#         )
#         return not is_subscribed


class SubscribeFilter(Filter):
    async def __call__(self, event: Message | CallbackQuery, bot: Bot) -> bool:
        return True
        