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
        """
        Construct daily report.

        :return: The constructed report as a string.
        """
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
        source_counts = await self._history_dao.get_counts_by_source()

        source_lines = []
        if source_counts:
            for source, count in source_counts:
                if source.value.upper() == "INSTAGRAM_INSTALOADER":
                    continue
                source_lines.append(f"<b>{source.value.upper()}:</b> {count:,}")
        source_stats_str = "\n".join(source_lines)
        for key, value in {"YMDANTIC": "YANDEX MUSIC"}.items():
            source_stats_str = source_stats_str.replace(key, value)
        return _("stats_report_template").format(
            date=str((datetime.now(tz=timezone.utc) + timedelta(hours=3)).date()),
            new_users_24h=f"{new_users_24h:,}",
            active_users_24h=f"{active_users_24h:,}",
            downloads_24h=f"{downloads_24h:,}",
            total_users=f"{total_users:,}",
            total_downloads=f"{total_downloads:,}",
            source_stats=source_stats_str,
        )
