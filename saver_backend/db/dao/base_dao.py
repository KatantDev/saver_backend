from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from saver_backend.db.dependencies import get_db_session


class BaseDAO:
    """Abstract base class for all DAOs."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session
