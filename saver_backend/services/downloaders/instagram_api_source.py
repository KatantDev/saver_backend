import asyncio
import functools
import logging
import secrets
from pathlib import Path
from typing import Any, Callable

import ffmpeg
import instagrapi
from instagrapi.exceptions import LoginRequired
from instagrapi.types import Resource, Story

from saver_backend.entities.enums import InstagramContentTypeEnum, SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import PhotoDTO, VideoDTO
from saver_backend.settings import settings


def retry_on_loginrequired(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Retry function on LoginRequired up to 3 times.

    :param func: Function to retry.
    :return: Wrapped function.
    """

    @functools.wraps(func)
    def _wrapper(
        self: "InstagramAPIController",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        retry_count = 0
        while True:
            try:
                return func(self, *args, **kwargs)
            except LoginRequired:
                retry_count += 1
                logging.warning(
                    "LoginRequired in %s, reloading settings and retrying (attempt %s)",
                    func.__name__,
                    retry_count,
                )
                if retry_count >= 3:
                    raise

                self._session_path.unlink(missing_ok=True)
                self.load_settings()

    return _wrapper


class InstagramAPIController(BaseSourceController):
    """Asynchronous controller for downloading from Instagram through instaloader."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._download_directory = BASE_DOWNLOAD_PATH / SourceEnum.INSTAGRAM_API.value
        self._download_directory.mkdir(exist_ok=True)

        self._api = instagrapi.Client()
        self._session_path = self.load_settings()
        self._loop = asyncio.get_event_loop()

    def load_settings(self) -> Path:
        """
        Load settings.

        If settings file exists, load settings from file.
        If settings file does not exist, login and dump settings to file.

        :return: Path to settings file.
        """
        credentials = secrets.choice(settings.instagram_accounts)
        index = settings.instagram_accounts.index(credentials)
        session_path = Path(
            f"cookies/{SourceEnum.INSTAGRAM_API.value}/cookies{index + 1}.json",
        )

        if session_path.exists():
            self._api.load_settings(session_path)
        else:
            self._api = instagrapi.Client()
            login, password = credentials.split(":")
            self._api.login(login, password)
            self._api.dump_settings(session_path)
            self._process_percent(percent=100 // 5)
        return session_path

    @staticmethod
    def _get_video_dimensions(path_or_url: str | Path) -> tuple[int, int, int]:
        probe = ffmpeg.probe(path_or_url)
        video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
        w = int(video_stream["width"])
        h = int(video_stream["height"])
        duration = int(float(probe["format"]["duration"]))
        return w, h, duration

    @retry_on_loginrequired
    def _download_resource(self, resource: Resource | Story) -> PhotoDTO | VideoDTO:
        """
        Download resource.

        :param resource: Resource or story.
        """
        url = str(
            resource.thumbnail_url if resource.media_type == 1 else resource.video_url,
        )
        file_path = self._api.story_download_by_url(
            url=url,
            folder=self._download_directory,
        )
        if resource.media_type == 1:
            return PhotoDTO(path=file_path, url=self._resolution.url)

        width, height, duration = self._get_video_dimensions(file_path)
        thumbnail_path = self._api.story_download_by_url(
            url=str(resource.thumbnail_url),
            folder=self._download_directory,
        )
        return VideoDTO(
            path=file_path,
            width=width,
            height=height,
            duration=duration,
            thumbnail=thumbnail_path,
            url=self._resolution.url,
        )

    @retry_on_loginrequired
    def _download_story(self, code: str) -> None:
        """
        Download story.

        :param code: Code of the media.
        :return: Dictionary with media information.
        """
        base_percent = round(100 / 3)

        story_info = self._api.story_info(story_pk=code)
        self._process_percent(percent=base_percent)

        result = self._download_resource(story_info)
        self._process_percent(percent=base_percent + round(100 / 2))

        if isinstance(result, PhotoDTO):
            photo_coro = self._telegram_bot_controller.send_finish_downloading_photo(
                photo=result,
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )
            asyncio.run_coroutine_threadsafe(photo_coro, self._loop)
        else:
            video_coro = self._telegram_bot_controller.send_finish_downloading(
                video=result,
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )
            asyncio.run_coroutine_threadsafe(video_coro, self._loop)

    @retry_on_loginrequired
    def _download_post(self, code: str) -> None:
        """
        Download post.

        :param code: Code of the media.
        """
        base_percent = round(100 / 3)

        post = self._api.media_info(media_pk=self._api.media_pk_from_code(code))
        self._process_percent(percent=base_percent)

        count_medias = len(post.resources)
        files: list[PhotoDTO | VideoDTO] = []
        for index, resource in enumerate(post.resources):
            files.append(self._download_resource(resource))

            # Default percent is ~33% (100 / 3)
            # and downloading percent is ~50% (100 / 2)
            percent_downloading = round(100 / 2 / count_medias * (index + 1))
            self._process_percent(percent=base_percent + percent_downloading)
        coro = self._telegram_bot_controller.send_finish_downloading_group(
            files=files,
            telegram_id=self._telegram_id,
            message_id=self._message_id,
        )
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def download_video(self) -> None:
        """
        Asynchronously downloads a video from Instagram.

        :return: Dictionary with information about the downloaded file.
        """
        await asyncio.to_thread(self.load_settings)

        code = self._resolution.metadata.get("code")
        if code is None:
            logging.error("No code found in metadata")
            return
        content_type = self._resolution.metadata.get("type")

        if content_type == InstagramContentTypeEnum.STORIES:
            await asyncio.to_thread(self._download_story, code=code)
        if content_type == InstagramContentTypeEnum.POST:
            await asyncio.to_thread(self._download_post, code=code)
