from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from saver_backend.services.telegram.daily_report_service import DailyReportService
from saver_backend.telegram_bot.filters.admin import AdminFilter

stats_router = Router()


@stats_router.message(Command("stats"), AdminFilter())
async def on_stats_command(
    message: Message,
    daily_report_service: DailyReportService,
) -> None:
    """
    Handle /stats command for admins.

    :param message: Message object.
    :param daily_report_service: DailyReportService instance from middleware.
    """
    report = await daily_report_service.construct()
    await message.answer(report)
