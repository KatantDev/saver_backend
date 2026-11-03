from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from saver_backend.db.dependencies import get_db_session


class BaseDAO:
    """
    Abstract base class for all DAOs.

    Every DAO method ends its own transaction. Tasks and bot handlers keep
    one session open while they download and upload media for minutes, and
    an open transaction would pin a database connection for all that time.
    """

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def _release(self) -> None:
        """Commit the current transaction and return its connection to the pool."""
        await self.session.commit()
