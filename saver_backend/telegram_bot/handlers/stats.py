from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from saver_backend.db.dao.history_dao import HistoryDAO
from saver_backend.db.dao.user_dao import UserDAO
from saver_backend.services.telegram.daily_report_service import DailyReportService
from saver_backend.telegram_bot.filters.admin import AdminFilter

stats_router = Router()


@stats_router.message(Command("stats"), AdminFilter())
async def on_stats_command(
    message: Message,
    user_dao: UserDAO,
    history_dao: HistoryDAO,
) -> None:
    """
    Handle /stats command for admins.

    :param message: Message object.
    :param user_dao: UserDAO instance.
    :param history_dao: HistoryDAO instance.
    """
    service = DailyReportService(user_dao=user_dao, history_dao=history_dao)
    report = await service.construct()
    await message.answer(report)
