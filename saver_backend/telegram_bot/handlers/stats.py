from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from saver_backend.db.dao.request_log_dao import RequestLogDAO
from saver_backend.services.telegram.daily_report_service import DailyReportService
from saver_backend.services.telegram.request_report_service import RequestReportService
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


@stats_router.message(Command("requests"), AdminFilter())
async def on_requests_command(
    message: Message,
    request_log_dao: RequestLogDAO,
) -> None:
    """
    Handle /requests command for admins: what users try to download.

    Covers every request, including unsupported sites, unlike /stats, which
    counts only successful downloads. Accepts an optional number of days,
    e.g. "/requests 7" (default 1, capped at 90).

    :param message: Message object.
    :param request_log_dao: RequestLogDAO instance from middleware.
    """
    days = 1
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].isdigit():
        days = max(1, min(int(parts[1]), 90))
    report = await RequestReportService(request_log_dao).construct(days=days)
    await message.answer(report)
