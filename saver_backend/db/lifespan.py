from fastapi.datastructures import State as FastAPIState
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from taskiq import TaskiqState

from saver_backend.settings import settings


def init_db(state: FastAPIState | TaskiqState) -> async_sessionmaker[AsyncSession]:
    """
    Creates connection to the database.

    This function creates SQLAlchemy engine instance,
    session_factory for creating sessions
    and stores them in the application's state property.

    :param state: State of taskiq or fastapi.
    """
    engine = create_async_engine(
        str(settings.db_url),
        echo=settings.db_echo,
        pool_size=20,
        max_overflow=10,
    )
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    state.db_engine = engine
    state.db_session_factory = session_factory
    return session_factory


async def shutdown_db(state: FastAPIState | TaskiqState) -> None:
    """
    Shutdown database connection.

    :param state: State of taskiq or fastapi.
    """
    await state.db_engine.dispose()
