import asyncio

import sentry_sdk
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import ResultChatMemberUnion

from saver_backend.settings import settings


async def check_subscriptions(
    bot: Bot,
    telegram_id: int,
) -> bool:
    """
    Check subscriptions.

    :param bot: Bot.
    :param telegram_id: Telegram ID.
    :return: True if subscribed, False otherwise.
    """
    tasks = []
    for channel in settings.subscription_channels:
        tasks.append(
            bot.get_chat_member(
                chat_id=f"@{channel}",
                user_id=telegram_id,
            ),
        )
    results: list[ResultChatMemberUnion | BaseException] = await asyncio.gather(
        *tasks,
        return_exceptions=True,
    )

    subscribed = True
    for result in results:
        if isinstance(result, BaseException):
            sentry_sdk.capture_exception(result)
            subscribed = False
            break
        if result.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
            subscribed = False
            break
    return subscribed
