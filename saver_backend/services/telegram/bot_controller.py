import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Awaitable, Sequence, cast

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
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    FSInputFile,
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
    InlineQueryResultVideo,
    InputTextMessageContent,
    Message,
    Update,
    URLInputFile,
    Video,
)
from aiogram.utils.i18n import I18n, SimpleI18nMiddleware
from aiogram.utils.media_group import MediaGroupBuilder
from redis.asyncio import Redis
from sentry_sdk import capture_exception
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from saver_backend.db.models.cache_model import CacheModel
from saver_backend.services.downloaders.schema import (
    AudioDTO,
    PhotoDTO,
    VideoDTO,
)
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings
from saver_backend.telegram_bot.keyboards import inline
from saver_backend.telegram_bot.middlewares.controller_provider import (
    ControllerProviderMiddleware,
)
from saver_backend.telegram_bot.middlewares.dao_provider import DAOProviderMiddleware
from saver_backend.telegram_bot.middlewares.database import DatabaseProviderMiddleware
from saver_backend.telegram_bot.middlewares.user import UserMiddleware


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
            timeout=180,
        )
        self._bot = Bot(
            session=self._session,
            token=settings.telegram_bot_token,
            default=default,
            **kwargs,
        )
        self.language: str | None = None

        self._redis = Redis.from_url(str(settings.redis_url))
        storage = RedisStorage(redis=self._redis)
        self._dispatcher = Dispatcher(
            main_bot=self._bot,
            storage=storage,
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

    @property
    def i18n(self) -> I18n:
        """
        Get I18n instance.

        :return: I18n instance.
        """
        return self._i18n

    async def close(self) -> None:
        """Close bot session."""
        await self._bot.session.close()
        await self._redis.close()

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
            logging.exception(e)
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
        self._dispatcher.update.outer_middleware(
            DatabaseProviderMiddleware(session_factory=session_factory),
        )
        self._dispatcher.update.outer_middleware(DAOProviderMiddleware())
        self._dispatcher.update.outer_middleware(
            ControllerProviderMiddleware(controller=self),
        )
        self._dispatcher.update.outer_middleware(UserMiddleware())
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
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await self._send(coro=coro)
        except Exception as e:
            if settings.environment == "local":
                logging.exception(e)
            capture_exception(e)

    async def set_fsm_data(
        self,
        user_id: int,
        chat_id: int,
        data: dict[str, Any],
    ) -> None:
        """
        Set data to FSM context for a specific user and chat.

        :param user_id: The user's Telegram ID.
        :param chat_id: The chat's Telegram ID.
        :param data: The data to store in FSM.
        """
        context = self._dispatcher.fsm.resolve_context(
            bot=self.bot,
            chat_id=chat_id,
            user_id=user_id,
        )
        if not context:
            logging.warning(
                "Failed to resolve FSM context for user_id=%s, chat_id=%s",
                user_id,
                chat_id,
            )
            return

        await context.set_data(data)

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
        language: str | None = None,
    ) -> None:
        """
        Send welcome message.

        :param chat_id: Chat id.
        :param language: Language code.
        """
        coro = self._bot.send_message(
            chat_id=chat_id,
            text=_("welcome message", locale=language or self.language),
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
            logging.error(e)
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
            chat_id=telegram_id,
            message_id=message_id,
            text=f"Downloading... {percent}%",
        )
        await self._send(coro)

    async def answer_inline_query_cached_video(
        self,
        inline_query_id: str,
        video_dto: VideoDTO,
        file_id: str,
    ) -> None:
        """
        Answer an inline query with a cached video using file_id.

        :param inline_query_id: ID of the inline query.
        :param video_dto: DTO of the video.
        :param file_id: ID of the file.
        """
        result = InlineQueryResultCachedVideo(
            id=str(uuid.uuid4()),
            video_file_id=file_id,
            title=video_dto.title or "Video",
            description=video_dto.description,
            caption=_("result direct message").format(
                url=video_dto.url,
                title=self._format_title_html(video_dto),
            ),
        )
        coro = self._bot.answer_inline_query(
            inline_query_id=inline_query_id,
            results=[result],
            cache_time=0,
        )
        await self._send(coro)

    async def answer_inline_query_video(
        self,
        inline_query_id: str,
        video_dto: VideoDTO,
    ) -> None:
        """
        Answer an inline query with a video using a direct URL.

        :param inline_query_id: ID of the inline query.
        :param video_dto: DTO of the video.
        """
        if not video_dto.direct_download_url or not video_dto.thumbnail_url:
            await self.answer_inline_query_error(inline_query_id)
            return

        result = InlineQueryResultVideo(
            id=str(uuid.uuid4()),
            video_url=video_dto.direct_download_url,
            thumbnail_url=video_dto.thumbnail_url,
            mime_type="video/mp4",
            title=video_dto.title or "Video",
            description=video_dto.description or "via @saver",
            caption=_("result direct message").format(url=video_dto.url, title=""),
        )
        coro = self._bot.answer_inline_query(
            inline_query_id=inline_query_id,
            results=[result],
            cache_time=0,
        )
        await self._send(coro)

    async def answer_inline_query_error(
        self,
        inline_query_id: str,
        error_text: str | None = None,
    ) -> None:
        """
        Answer an inline query with an error message.

        :param inline_query_id: ID of the inline query.
        :param error_text: Error message.
        """
        text = error_text or _("error downloading inline query")

        result = InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=_("error inline query"),
            description=text,
            input_message_content=InputTextMessageContent(
                message_text=text,
                parse_mode=self._bot.default.parse_mode,
            ),
        )
        coro = self._bot.answer_inline_query(
            inline_query_id=inline_query_id,
            results=[result],
            cache_time=0,
        )
        await self._send(coro)

    async def _build_and_send_media_chunk(
        self,
        chunk: Sequence[PhotoDTO | VideoDTO],
        chat_id: int,
        caption: str | None = None,
    ) -> None:
        """
        Build a MediaGroup from a chunk of files and send it.

        :param chunk: A list of PhotoDTO or VideoDTO objects (max 10).
        :param chat_id: The chat ID to send the media group to.
        :param caption: The caption for the media group (if any).
        """
        media_group = MediaGroupBuilder(caption=caption)
        for file in chunk:
            media_input: str | FSInputFile | URLInputFile | None = None
            if isinstance(file, PhotoDTO):
                media_input = file.media_url or (
                    FSInputFile(path=file.path) if file.path else None
                )
            elif isinstance(file, VideoDTO):
                media_input = file.direct_download_url or (
                    FSInputFile(path=file.path) if file.path else None
                )

            if not media_input:
                continue

            if isinstance(file, PhotoDTO):
                media_group.add_photo(media=media_input)
            elif isinstance(file, VideoDTO):
                media_group.add_video(
                    media=media_input,
                    width=file.width,
                    height=file.height,
                    duration=file.duration,
                    thumbnail=(
                        FSInputFile(path=file.thumbnail) if file.thumbnail else None
                    ),
                    supports_streaming=True,
                )

        if media_group.build():
            coro = self._bot.send_media_group(
                chat_id=chat_id,
                media=media_group.build(),
            )
            await self._send(coro)

    async def send_finish_downloading_group(
        self,
        files: Sequence[PhotoDTO | VideoDTO],
        telegram_id: int,
        message_id: int | None = None,
        language: str | None = None,
    ) -> None:
        """
        Send finish downloading group message.

        :param files: List of files.
        :param telegram_id: Telegram ID of the user.
        :param message_id: Message ID.
        :param language: Language code.
        """
        chunk_size = 10
        total_files = len(files)

        caption = _(
            "result direct message",
            locale=language or self.language,
        ).format(url=files[0].url, title="")

        for i in range(0, total_files, chunk_size):
            chunk = files[i : i + chunk_size]

            await self._build_and_send_media_chunk(
                chunk=chunk,
                chat_id=telegram_id,
                caption=caption,
            )

        if message_id is not None:
            coro2 = self._bot.delete_message(
                message_id=message_id,
                chat_id=telegram_id,
            )
            await self._send(coro2)

    async def send_finish_downloading_audio(
        self,
        audio: AudioDTO,
        telegram_id: int,
        message_id: int | None = None,
        language: str | None = None,
    ) -> None:
        """
        Send finish downloading audio message.

        :param audio: Audio DTO.
        :param telegram_id: Telegram ID of the user.
        :param message_id: Message ID to delete after sending.
        :param language: Language code.
        """
        audio_input: URLInputFile | FSInputFile | None = None
        if audio.media_url:
            audio_input = URLInputFile(
                url=audio.media_url,
                filename=audio.title,
            )
        elif audio.path:
            audio_input = FSInputFile(path=audio.path)

        if not audio_input:
            logging.error(
                "Cannot send audio: No media_url or valid file path provided.",
            )
            if message_id:
                await self.delete_message(telegram_id, message_id)
            return

        caption = _(
            "result direct message",
            locale=language or self.language,
        ).format(url=audio.url, title="")

        coro = self._bot.send_audio(
            chat_id=telegram_id,
            audio=audio_input,
            caption=caption,
            title=audio.title,
            duration=audio.duration,
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
        language: str | None = None,
    ) -> None:
        """
        Send finish downloading message.

        :param photo: Photo.
        :param telegram_id: Telegram ID of the user.
        :param message_id: Message ID.
        :param language: Language of the photo.
        """
        photo_input = photo.media_url or (
            FSInputFile(path=photo.path) if photo.path else None
        )
        if not photo_input:
            logging.error("Cannot send photo: No media_url or path provided.")
            return

        coro = self._bot.send_photo(
            chat_id=telegram_id,
            photo=photo_input,
            caption=_(
                "result direct message",
                locale=language or self.language,
            ).format(url=photo.url, title=""),
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
        language: str | None = None,
    ) -> Video | None:
        """
        Send finish downloading message.

        :param video: VideoDTO.
        :param telegram_id: Telegram ID of the user.
        :param message_id: Message ID.
        :param supports_streaming: Supports streaming.
        :param language: Language of the video.
        :return: Sent video or None on failure.
        """
        video_input: str | FSInputFile | URLInputFile
        thumbnail_input: str | FSInputFile | None = None
        if video.direct_download_url:
            logging.info("Sending video via direct URL: %s", video.direct_download_url)
            video_input = (
                video.direct_download_url
                if video.duration and video.duration < 180
                else URLInputFile(url=video.direct_download_url)
            )
            thumbnail_input = video.thumbnail_url
        elif video.path and video.path.exists():
            logging.info("Sending video via file upload: %s", video.path)
            video_input = FSInputFile(path=video.path)
            if video.thumbnail and Path(video.thumbnail).exists():
                thumbnail_input = FSInputFile(path=video.thumbnail)
        else:
            logging.error(
                "Cannot send video: No direct URL or valid file path provided.",
            )
            return None

        try:
            message = await self._bot.send_video(
                chat_id=telegram_id,
                video=video_input,
                caption=_(
                    "result direct message",
                    locale=language or self.language,
                ).format(url=video.url, title=self._format_title_html(video)),
                width=video.width,
                height=video.height,
                duration=video.duration,
                cover=thumbnail_input,
                supports_streaming=supports_streaming,
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            return None
        except TelegramNetworkError:
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

    def _format_title_html(self, video: VideoDTO) -> str:
        """Format title as html string."""
        if video.channel:
            title_html = (
                f'📹 {video.title} <a href="{video.url}">→</a>\n'
                f'👤 {video.channel} <a href="{video.channel_url}">→</a>'
                f"\n"
            )
        elif video.title:
            title_html = f'📹 {video.title} <a href="{video.url}">→</a>'
        else:
            title_html = ""
        return title_html

    async def send_video_by_file_id(
        self,
        telegram_id: int,
        cache_item: CacheModel,
        url: str,
        language: str | None = None,
    ) -> Video | None:
        """
        Send video by file_id from cache.

        :param telegram_id: Telegram ID of the user.
        :param file_id: The file_id to send.
        :param url: The original source URL for the caption.
        :param language: The language of the caption.
        :return: The sent Video object or None on failure.
        """
        if not cache_item or not isinstance(cache_item.meta_data_dto, VideoDTO):
            return None
        file_id = cache_item.file_id
        try:
            message = await self._bot.send_video(
                chat_id=telegram_id,
                video=file_id,
                caption=_(
                    "result direct message",
                    locale=language or self.language,
                ).format(
                    url=url,
                    title=self._format_title_html(cache_item.meta_data_dto),
                ),
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

    async def send_x_fallback_message(
        self,
        telegram_id: int,
        fixed_url: str,
        language: str | None = None,
    ) -> None:
        """
        Send a fallback message with a modified URL for X/Twitter.

        :param telegram_id: Telegram ID of the user.
        :param fixed_url: The URL with the domain replaced by 'fixupx.com'.
        :param language: The language for the message.
        """
        text = _("result direct message", locale=language or self.language).format(
            url=fixed_url,
            title="",
        )
        coro = self._bot.send_message(chat_id=telegram_id, text=text)
        await self._send(coro)

    async def send_content_not_found_error(
        self,
        telegram_id: int,
        language: str | None = None,
    ) -> None:
        """
        Send a message indicating the content is private, deleted or not found.

        :param telegram_id: Telegram ID of the user.
        :param language: Language for the message.
        """
        coro = self._bot.send_message(
            chat_id=telegram_id,
            text=_("content private or not found", locale=language or self.language),
        )
        await self._send(coro)

    async def send_tiktok_error_downloading(
        self,
        telegram_id: int,
        language: str | None = None,
    ) -> None:
        """
        Send TikTok error downloading message.

        :param telegram_id: Telegram ID of the user.
        :param language: Language for message
        """
        coro = self._bot.send_message(
            chat_id=telegram_id,
            text=_("tiktok photo unsupported", locale=language or self.language),
        )
        await self._send(coro)

    async def send_error_downloading(
        self,
        telegram_id: int,
        language: str | None = None,
    ) -> None:
        """
        Send error downloading message.

        :param telegram_id: Telegram ID of the user.
        :param language: Language for message.
        """
        coro = self._bot.send_message(
            chat_id=telegram_id,
            text=_("error downloading", locale=language or self.language),
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

    async def edit_failed_video_info(
        self,
        telegram_id: int,
        message_id: int,
        language: str | None = None,
    ) -> None:
        """
        Edit failed video information.

        :param telegram_id: Telegram ID of the user.
        :param message_id: Message ID.
        :param language: Language for message.
        """
        coro = self._bot.edit_message_text(
            message_id=message_id,
            chat_id=telegram_id,
            text=_("failed to get video info", locale=language or self.language),
        )
        await self._send(coro)

    async def edit_video_no_formats(
        self,
        telegram_id: int,
        message_id: int,
        language: str | None = None,
    ) -> None:
        """
        Edit failed video information.

        :param telegram_id: Telegram ID of the user.
        :param message_id: Message ID.
        :param language: Language for message.
        """
        coro = self._bot.edit_message_text(
            message_id=message_id,
            chat_id=telegram_id,
            text=_("video info without formats", locale=language or self.language),
        )
        await self._send(coro)

    async def send_choose_quality(
        self,
        telegram_id: int,
        video_dto: VideoDTO,
        language: str | None = None,
    ) -> Message | None:
        """
        Send choose quality.

        If thumbnail_url is provided, message will include photo.
        Otherwise, just text.

        :param telegram_id: Telegram ID of the user.
        :param video_dto: Video DTO.
        :param language: The language of the caption.
        """
        text = _(
            "choose quality",
            locale=language or self.language,
        ).format(title=self._format_title_html(video_dto))
        reply_markup = inline.get_video_formats_keyboard(
            labels=video_dto.unique_labels,
        )

        message: Message | None = None

        if video_dto.thumbnail_url:
            try:
                message = await self._bot.send_photo(
                    chat_id=telegram_id,
                    photo=video_dto.thumbnail_url,
                    caption=text,
                    reply_markup=reply_markup,
                )
            except (TelegramBadRequest, AiogramError) as e:
                logging.warning(
                    "Failed to send quality selection with photo for user %s: %s. "
                    "Falling back to text-only message.",
                    telegram_id,
                    e,
                )

        if not message:
            try:
                message = await self._bot.send_message(
                    chat_id=telegram_id,
                    text=text,
                    reply_markup=reply_markup,
                )
            except AiogramError as e:
                logging.error(
                    "Failed to send text-only quality selection to user %s: %s",
                    telegram_id,
                    e,
                )
                capture_exception(e)
                return None

        return message
