import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from saver_backend.db.dao.history_dao import HistoryDAO
from saver_backend.db.dao.user_dao import UserDAO
from saver_backend.entities.enums import SourceEnum


async def create_user(user_dao: UserDAO, telegram_id: int) -> None:
    """Create a user with placeholder names."""
    await user_dao.create(
        telegram_id=telegram_id,
        first_name="Test",
        last_name=None,
        username=None,
        language_code="ru",
    )


@pytest.mark.anyio
async def test_dao_calls_do_not_keep_a_transaction(dbsession: AsyncSession) -> None:
    """A task downloads for minutes between DAO calls, holding no connection."""
    user_dao = UserDAO(dbsession)
    history_dao = HistoryDAO(dbsession)

    await create_user(user_dao, telegram_id=1001)
    assert not dbsession.in_transaction()

    await user_dao.update(
        telegram_id=1001,
        first_name="Renamed",
        last_name=None,
        username=None,
        language_code="en",
    )
    assert not dbsession.in_transaction()

    user = await user_dao.get_by_id(telegram_id=1001)
    assert user is not None
    assert user.lang == "en"
    assert not dbsession.in_transaction()

    await history_dao.create(
        user_id=user.id,
        source=SourceEnum.TIKTOK,
        url="https://vt.tiktok.com/example/",
    )
    assert not dbsession.in_transaction()

    assert await history_dao.get_count() == 1
    assert not dbsession.in_transaction()


@pytest.mark.anyio
async def test_locked_read_keeps_the_transaction(dbsession: AsyncSession) -> None:
    """A row read FOR UPDATE stays locked until the caller ends the transaction."""
    user_dao = UserDAO(dbsession)
    await create_user(user_dao, telegram_id=1002)

    await user_dao.get_by_id(telegram_id=1002, with_for_update=True)

    assert dbsession.in_transaction()
