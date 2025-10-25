import asyncio
import logging
from typing import Any, Awaitable, cast

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    AiogramError,
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import FSInputFile, Update, Video
from aiogram.utils.i18n import I18n, SimpleI18nMiddleware
from aiogram.utils.media_group import MediaGroupBuilder
from sentry_sdk import capture_exception
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from saver_backend.services.downloaders.schema import (
    PhotoDTO,
    VideoDTO,
)
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.telegram_bot.middlewares.dao_provider import DAOProviderMiddleware
from saver_backend.telegram_bot.middlewares.database import DatabaseProviderMiddleware


class TelegramBotController:
    """
    Telegram bot controller.

    Features:
    - Configures bot and dispatcher.
    - Provides methods to send messages.
    - Provides method to set webhook.
    - Provides method to check if secret token is valid.
    """

    def __init__(
        self,
        i18n: I18n,
        default: DefaultBotProperties = DefaultBotProperties(parse_mode=ParseMode.HTML),
        **kwargs: Any,
    ) -> None:
        self._i18n = i18n
        self._session = AiohttpSession(
            api=TelegramAPIServer.from_base(base=settings.telegram_bot_api_url),
        )
        self._bot = Bot(
            session=self._session,
            token=settings.telegram_bot_token,
            default=default,
            **kwargs,
        )
        self._dispatcher = Dispatcher(
            main_bot=self._bot,
            telegram_bot_controller=self,
        )

    @property
    def bot(self) -> Bot:
        """
        Get bot instance.

        :return: Bot instance.
        """
        return self._bot

    @property
    def dispatcher(self) -> Dispatcher:
        """
        Get dispatcher instance.

        :return: Dispatcher instance.
        """
        return self._dispatcher

    async def close(self) -> None:
        """Close bot session."""
        await self._bot.session.close()

    async def startup(self, **kwargs: Any) -> bool:
        """
        Startup bot by setting the webhook.

        :param kwargs: Additional arguments.
        :return: True if bot was started successfully, False otherwise.
        """
        return await self._startup_bot(bot=self._bot, **kwargs)

    @staticmethod
    async def _startup_bot(
        bot: Bot,
        **kwargs: Any,
    ) -> bool:
        """
        Set webhook for telegram bot.

        :param kwargs: Additional arguments.
        :return: True if webhook was set successfully, False otherwise.
        """
        try:
            await bot.set_webhook(
                url=settings.webhook_telegram_url + f"/{bot.token}",
                secret_token=settings.telegram_secret_token,
                drop_pending_updates=settings.environment != "prod",
                **kwargs,
            )
        except TelegramRetryAfter:
            pass
        except TelegramAPIError as e:
            capture_exception(e)
            logging.error(f"Failed to set webhook: {e}")
            return False
        logging.info(f"Set webhook to {settings.webhook_telegram_url}")
        return True

    def setup_middlewares(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """
        Setup middlewares for dispatcher.

        :param session_factory: Session factory.
        """
        self._dispatcher.update.middleware(
            DatabaseProviderMiddleware(session_factory=session_factory),
        )
        self._dispatcher.update.middleware(DAOProviderMiddleware())
        SimpleI18nMiddleware(self._i18n).setup(router=self._dispatcher)

    async def feed_update(self, update: Update) -> None:
        """
        Feed update to dispatcher.

        :param update: Update.
        """
        await self._dispatcher.feed_update(bot=self._bot, update=update)

    @staticmethod
    def is_valid_webhook_secret(secret_token: str | None) -> bool:
        """
        Check if secret token is valid.

        :param secret_token: Secret token.
        :return: True if secret token is valid, False otherwise
        """
        return settings.telegram_secret_token == secret_token

    async def _send(self, coro: Awaitable[Any]) -> None:
        """
        Wrapper for request to telegram bot.

        :param coro: Coroutine to execute.
        """
        try:
            await coro
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await self._send(coro=coro)
        except Exception as e:
            if settings.environment == "local":
                logging.exception(e)
            capture_exception(e)

    async def get_username(self) -> str:
        """
        Get username of bot.

        :return: Username of bot.
        """
        me = await self._bot.me()
        return cast(str, me.username)

    async def send_welcome_message(
        self,
        chat_id: int,
    ) -> None:
        """
        Send welcome message.

        :param chat_id: Chat id.
        """
        coro = self._bot.send_message(
            chat_id=chat_id,
            text=_("welcome message"),
        )
        return await self._send(coro)

    async def send_start_downloading(
        self,
        telegram_id: int,
        percent: int,
    ) -> int | None:
        """
        Send start downloading message.

        :param telegram_id: Telegram ID of the user.
        :param percent: Percent of the video.
        """
        try:
            message = await self._bot.send_message(
                chat_id=telegram_id,
                text=f"Downloading... {percent}%",
            )
            return message.message_id
        except AiogramError as e:
            capture_exception(e)
            logging.error(f"Failed to send start downloading message: {e}")
            return None

    async def send_update_downloading(
        self,
        telegram_id: int,
        message_id: int,
        percent: int,
    ) -> None:
        """
        Send update downloading message.

        :param telegram_id: Telegram ID of the user.
        :param message_id: Message ID.
        :param percent: Percent of the video.
        """
        coro = self._bot.edit_message_text(
            message_id=message_id,
            chat_id=telegram_id,
            text=f"Downloading... {percent}%",
        )
        return await self._send(coro)

    async def send_finish_downloading_group(
        self,
        files: list[PhotoDTO | VideoDTO],
        telegram_id: int,
        message_id: int | None = None,
    ) -> None:
        """
        Send finish downloading group message.

        :param files: List of files.
        :param telegram_id: Telegram ID of the user.
        :param message_id: Message ID.
        """
        media_group = MediaGroupBuilder(
            caption=_("result direct message").format(url=files[0].url),
        )
        for file in files:
            if isinstance(file, PhotoDTO):
                media_group.add_photo(media=FSInputFile(path=file.path))
            elif isinstance(file, VideoDTO):
                media_group.add_video(
                    media=FSInputFile(path=file.path),
                    width=file.width,
                    height=file.height,
                    duration=file.duration,
                    thumbnail=(
                        FSInputFile(path=file.thumbnail) if file.thumbnail else None
                    ),
                    supports_streaming=True,
                )

        coro = self._bot.send_media_group(
            chat_id=telegram_id,
            media=media_group.build(),
        )
        await self._send(coro)
        if message_id is not None:
            coro2 = self._bot.delete_message(
                message_id=message_id,
                chat_id=telegram_id,
            )
            await self._send(coro2)

    async def send_finish_downloading_photo(
        self,
        photo: PhotoDTO,
        telegram_id: int,
        message_id: int | None = None,
    ) -> None:
        """
        Send finish downloading message.

        :param photo: Photo.
        :param telegram_id: Telegram ID of the user.
        :param message_id: Message ID.
        """
        coro = self._bot.send_photo(
            chat_id=telegram_id,
            photo=FSInputFile(path=photo.path),
            caption=_("result direct message").format(url=photo.url),
        )
        await self._send(coro)
        if message_id is not None:
            coro2 = self._bot.delete_message(
                message_id=message_id,
                chat_id=telegram_id,
            )
            await self._send(coro2)

    async def send_finish_downloading(
        self,
        video: VideoDTO,
        telegram_id: int,
        message_id: int | None = None,
        supports_streaming: bool = True,
    ) -> Video | None:
        """
        Send finish downloading message.

        :param video: VideoDTO.
        :param telegram_id: Telegram ID of the user.
        :param message_id: Message ID.
        :param supports_streaming: Supports streaming.
        """
        try:
            message = await self._bot.send_video(
                chat_id=telegram_id,
                video=FSInputFile(path=video.path),
                caption=_("result direct message").format(url=video.url),
                width=video.width,
                height=video.height,
                duration=video.duration,
                cover=(
                    FSInputFile(
                        path=video.thumbnail,
                    )
                    if video.thumbnail
                    else None
                ),
                supports_streaming=supports_streaming,
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            return None
        except Exception as e:
            if settings.environment == "local":
                logging.exception(e)
            capture_exception(e)
            return None

        if message_id is not None:
            coro2 = self._bot.delete_message(
                message_id=message_id,
                chat_id=telegram_id,
            )
            await self._send(coro2)

        return message.video

    async def send_video_by_file_id(
        self,
        telegram_id: int,
        file_id: str,
        url: str,
    ) -> Video | None:
        """
        Send video by file_id from cache.

        :param telegram_id: Telegram ID of the user.
        :param file_id: The file_id to send.
        :param url: The original source URL for the caption.
        :return: The sent Video object or None on failure.
        """
        try:
            message = await self._bot.send_video(
                chat_id=telegram_id,
                video=file_id,
                caption=_("result direct message").format(url=url),
            )
            return message.video
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logging.warning(
                "Failed to send video by file_id %s to user %s: %s. "
                "Cache might be invalid.",
                file_id,
                telegram_id,
                e,
            )
            return None
        except Exception as e:
            if settings.environment == "local":
                logging.exception(e)
            capture_exception(e)
            return None

    async def send_tiktok_error_downloading(self, telegram_id: int) -> None:
        """
        Send TikTok error downloading message.

        :param telegram_id: Telegram ID of the user.
        """
        coro = self._bot.send_message(
            chat_id=telegram_id,
            text=(
                "Фотографии из TikTok пока что не поддерживаются, "
                "следите за обновлениями."
            ),
        )
        await self._send(coro)

    async def delete_message(self, telegram_id: int, message_id: int) -> None:
        """
        Delete message.

        :param telegram_id: Telegram ID of the user.
        :param message_id: Message ID.
        """
        coro = self._bot.delete_message(
            message_id=message_id,
            chat_id=telegram_id,
        )
        await self._send(coro)
