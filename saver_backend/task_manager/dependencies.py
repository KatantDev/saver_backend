import logging
from typing import Annotated, AsyncGenerator

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from taskiq import TaskiqDepends, TaskiqState


async def get_session(
    state: Annotated[TaskiqState, TaskiqDepends()],
) -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session.

    :param state: current state.
    :return: database session.
    """
    session: AsyncSession = state.db_session_factory()
    try:
        yield session
    except IntegrityError as error:
        await session.rollback()
        logging.error(error)
    finally:
        await session.commit()
        await session.close()
