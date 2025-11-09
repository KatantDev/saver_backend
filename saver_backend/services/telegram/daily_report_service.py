from datetime import datetime, timedelta, timezone

from saver_backend.db.dao.history_dao import HistoryDAO
from saver_backend.db.dao.user_dao import UserDAO
from saver_backend.entities.enums import SourceEnum
from saver_backend.services.i18n import gettext as _


class DailyReportService:
    """Service for constructing daily report."""

    def __init__(self, user_dao: UserDAO, history_dao: HistoryDAO) -> None:
        self._user_dao = user_dao
        self._history_dao = history_dao

    @staticmethod
    def _clean_source_name(source: SourceEnum) -> str:
        """
        Clean source name from technical suffixes and format it.

        Example: YOUTUBE_VIDEO_YDL -> Youtube Video.

        :param source: The source enum member.
        :return: A cleaned, human-readable name.
        """
        name = source.name.lower()
        suffixes_to_remove = ["_ydl", "_api"]
        for suffix in suffixes_to_remove:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return name.replace("_", " ").title()

    async def construct(self) -> str:
        """Construct daily report."""
        one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)

        new_users_24h = await self._user_dao.get_count(created_after=one_day_ago)
        active_users_24h = await self._history_dao.get_active_users_count(
            created_after=one_day_ago,
        )
        downloads_24h = await self._history_dao.get_count(
            created_after=one_day_ago,
        )

        total_users = await self._user_dao.get_count()
        total_downloads = await self._history_dao.get_count()

        date_str = (datetime.now(tz=timezone.utc) + timedelta(hours=3)).strftime(
            "%Y-%m-%d",
        )
        report_lines = [
            _("stats_date_header").format(date=date_str),
            _("stats_new_users_24h").format(value=f"{new_users_24h:,}"),
            _("stats_active_users_24h").format(value=f"{active_users_24h:,}"),
            _("stats_downloads_24h").format(value=f"{downloads_24h:,}"),
            "",
            _("stats_total_users").format(value=f"{total_users:,}"),
            _("stats_total_downloads").format(value=f"{total_downloads:,}"),
        ]

        source_counts = await self._history_dao.get_counts_by_source()
        if source_counts:
            report_lines.append("")
            for source, count in source_counts:
                clean_name = self._clean_source_name(source)
                line = f"<b>{clean_name}:</b> {count:,}"
                report_lines.append(line)

        return "\n".join(report_lines)
