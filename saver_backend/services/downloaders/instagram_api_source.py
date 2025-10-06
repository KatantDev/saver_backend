import asyncio
import functools
import logging
from pathlib import Path
from typing import Any, Callable

import ffmpeg
import instagrapi
from instagrapi.exceptions import LoginRequired
from instagrapi.types import Resource, Story

from saver_backend.entities.enums import InstagramContentTypeEnum, SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import PhotoDTO, VideoDTO


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
        self._session_path = Path(
            f"cookies/{SourceEnum.INSTAGRAM_API.value}/cookies1.json",
        )

        self._api = instagrapi.Client()
        self._loop = asyncio.get_event_loop()

    def load_settings(self) -> None:
        """
        Load settings.

        If settings file exists, load settings from file.
        If settings file does not exist, login and dump settings to file.
        """
        logging.info("Loading settings from %s", self._session_path)
        if self._session_path.exists():
            self._api.load_settings(self._session_path)
        else:
            self._api.login("katantdev@yandex.ru", "coxdib-2nyxki-Kofwuh")
            self._api.dump_settings(self._session_path)

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
            return PhotoDTO(path=file_path)

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
        )

    @retry_on_loginrequired
    def _download_story(self, code: str) -> None:
        """
        Download story.

        :param code: Code of the media.
        :return: Dictionary with media information.
        """
        story_info = self._api.story_info(story_pk=code)
        result = self._download_resource(story_info)
        if isinstance(result, PhotoDTO):
            coro = self._telegram_bot_controller.send_finish_downloading_photo(
                photo=result,
                telegram_id=self._telegram_id,
            )
        else:
            coro = self._telegram_bot_controller.send_finish_downloading(
                video=result,
                telegram_id=self._telegram_id,
            )
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    @retry_on_loginrequired
    def _download_post(self, code: str) -> None:
        """
        Download post.

        :param code: Code of the media.
        """
        post = self._api.media_info(media_pk=self._api.media_pk_from_code(code))
        files: list[PhotoDTO | VideoDTO] = []
        for resource in post.resources:
            files.append(self._download_resource(resource))
        coro = self._telegram_bot_controller.send_finish_downloading_group(
            files=files,
            telegram_id=self._telegram_id,
        )
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def download_video(
        self,
        resolution: Resolution,
    ) -> None:
        """
        Asynchronously downloads a video from Instagram.

        :param resolution: Resolution of the video.
        :return: Dictionary with information about the downloaded file.
        """
        await asyncio.to_thread(self.load_settings)

        code = resolution.metadata.get("code")
        if code is None:
            logging.error("No code found in metadata")
            return
        content_type = resolution.metadata.get("type")

        if content_type == InstagramContentTypeEnum.STORIES:
            await asyncio.to_thread(self._download_story, code=code)
        if content_type == InstagramContentTypeEnum.POST:
            await asyncio.to_thread(self._download_post, code=code)
