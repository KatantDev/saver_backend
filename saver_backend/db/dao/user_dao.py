from datetime import datetime, timezone

from sqlalchemy import func, select, update

from saver_backend.db.dao.base_dao import BaseDAO
from saver_backend.db.models.user_model import UserModel


class UserDAO(BaseDAO):
    """Class for accessing user table."""

    async def create(
        self,
        telegram_id: int,
        first_name: str,
        last_name: str | None,
        username: str | None,
        language_code: str | None,
    ) -> UserModel:
        """
        Add single user to session.

        :param telegram_id: telegram id of a user.
        :param first_name: first name of a user.
        :param last_name: last name of a user.
        :param username: username of a user.
        :param language_code: language code of a user.
        """
        model = UserModel(
            first_name=first_name,
            last_name=last_name,
            telegram_id=telegram_id,
            username=username,
            created_at=datetime.now(tz=timezone.utc),
            lang=language_code,
        )
        self.session.add(model)
        await self.session.flush([model])
        return model

    async def update(
        self,
        telegram_id: int,
        first_name: str,
        last_name: str | None,
        username: str | None,
        language_code: str | None,
    ) -> None:
        """
        Update user in session.

        :param telegram_id: telegram id of a user.
        :param first_name: first name of a user.
        :param last_name: last name of a user.
        :param username: username of a user.
        :param language_code: language code of a user.
        """
        query = (
            update(UserModel)
            .where(UserModel.telegram_id == telegram_id)
            .values(
                first_name=first_name,
                last_name=last_name,
                username=username,
                lang=language_code,
            )
        )
        await self.session.execute(query)

    async def get_by_id(
        self,
        telegram_id: int,
        with_for_update: bool = False,
    ) -> UserModel | None:
        """
        Get user by telegram id.

        :param telegram_id: telegram id of a user.
        :param with_for_update: if True, lock the row for update.
        :return: user model.
        """
        query = select(UserModel).where(UserModel.telegram_id == telegram_id)
        if with_for_update:
            query = query.with_for_update()
        result = await self.session.execute(query)
        return result.scalar()

    async def get_count(self, created_after: datetime | None = None) -> int:
        """
        Get count of users.

        :param created_after: datetime of a user.
        :return: count of users.
        """
        query = select(func.count()).select_from(UserModel)
        if created_after:
            query = query.where(UserModel.created_at >= created_after)
        result = await self.session.execute(query)
        return result.scalar() or 0
