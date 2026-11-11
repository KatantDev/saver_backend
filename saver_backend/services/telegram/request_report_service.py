from datetime import datetime, timedelta, timezone

from saver_backend.db.dao.request_log_dao import RequestLogDAO
from saver_backend.entities.enums import SourceEnum


class RequestReportService:
    """
    Builds the admin report on download requests (what users try to download).

    Unlike the daily stats, which count only successful downloads, this covers
    every request, including unsupported sites. The text is built in code and
    does not go through i18n, so it does not depend on compiled translations.
    """

    def __init__(self, request_log_dao: RequestLogDAO) -> None:
        self._request_log_dao = request_log_dao

    @staticmethod
    def _fmt(number: int) -> str:
        return f"{number:,}".replace(",", " ")

    @staticmethod
    def _source_name(source: SourceEnum) -> str:
        name = source.value.lower()
        for suffix in ("_ydl", "_api"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        name = name.replace("_", " ").title()
        return name.replace("Ymdantic", "Yandex Music")

    async def construct(self, days: int = 1) -> str:
        """
        Build the request report for the last ``days`` days.

        :param days: Size of the window in days.
        :return: The report text (HTML).
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)

        total = await self._request_log_dao.get_count(created_after=since)
        by_source = await self._request_log_dao.get_counts_by_source(
            created_after=since,
        )
        top_unsupported = await self._request_log_dao.get_top_unsupported_hosts(
            created_after=since,
        )

        supported = [(s, c) for s, c in by_source if s != SourceEnum.UNSUPPORTED]
        unsupported_total = sum(c for s, c in by_source if s == SourceEnum.UNSUPPORTED)

        lines = [
            f"<b>Запросы за {days} дн.</b>",
            f"Всего запросов: {self._fmt(total)}",
            f"Поддерживаемые: {self._fmt(total - unsupported_total)}",
            f"Не поддерживаются: {self._fmt(unsupported_total)}",
            "",
            "<b>По площадкам:</b>",
        ]
        if supported:
            lines += [
                f"{self._source_name(source)}: {self._fmt(count)}"
                for source, count in supported
            ]
        else:
            lines.append("—")

        if top_unsupported:
            lines += ["", "<b>Не поддерживаются (хосты):</b>"]
            lines += [f"{host}: {self._fmt(count)}" for host, count in top_unsupported]

        return "\n".join(lines)
