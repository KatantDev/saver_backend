import logging
from pathlib import Path
from typing import Any, ClassVar

from httpx import AsyncClient, RequestError
from lxml.html import fromstring

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.browser_cdp import BrowserStart
from saver_backend.services.downloaders.schema import (
    SaveTikFromHtml,
    SaveTikResponse,
    VideoDTO,
)


class DouyinController(BaseSourceController):
    """Controller for downloading videos from TikTok via tikwm.com API."""

    SOURCE = SourceEnum.DOUYIN
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.ALL

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)
        self._temp_files: list[Path] = []
        self._video: VideoDTO | None = None
        self._web_url = "https://savetik.co"
        self._api_url = "https://savetik.co/api/"
        self._client = AsyncClient(proxy=self._proxy)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Fetch video info from SaveTik API.

        This method acts as the info-gathering step for SaveTik,
        returning the raw API response data for further processing.

        :param url: The Douyin video URL.
        :return: The 'data' part of the SaveTik API response or None on failure.
        """
        if self._proxy is None:
            return None
        cdp = BrowserStart(proxy=self._proxy)
        try:
            await cdp.start_cdp()
            await cdp.load_url(self._web_url)
            response = await cdp.fetch_js_url_encoded(
                url=self._api_url + "ajaxSearch",
                data={"q": url, "lang": "en", "cftoken": ""},
            )
            if response is None:
                return None
            if response.get("status") == "ok" and response.get("data"):
                return response

            logging.error(
                "SaveTik API returned an error: %s (URL: %s)",
                response.get("msg"),
                self._resolution.url,
            )
            return None
        except RequestError as e:
            logging.exception(e)
            return None
        finally:
            await cdp.cleanup_resources()

    def _parse_web_data(self, html: str) -> SaveTikFromHtml | None:
        """Parse Douyin video download links."""

        try:
            tree = fromstring(html)

            # Более сложные XPath выражения
            xpath_queries = {
                "title": ".//h3/text()",
                "id": ".//input[@id='TikTokId']/@value",
                "cover": './/div[contains(@class, "thumbnail")]//img/@src',
                "mp4": './/a[contains(text(), "Download MP4 [1]")]/@href',
                "mp4_hd": './/a[contains(text(), "Download MP4 HD")]/@href',
                "mp3": './/a[contains(text(), "Download MP3")]/@href',
            }

            result = {}
            for key, xpath in xpath_queries.items():
                elements = tree.xpath(xpath)
                if elements:
                    result[key] = elements[0]

            return SaveTikFromHtml.model_validate(result)

        except Exception as e:
            logging.exception(f"Failed to parse Douyin HTML: {e}; [html]: {html}")
            raise Exception from e

    async def _handle_video(self, data: SaveTikFromHtml) -> None:
        """
        Handle caching, downloading, and sending of a single video.

        :param data: The data to send.
        """
        if not data.mp4_hd or not data.cover:
            await self._send_error_message()
            return

        quality = "best"
        source_id = self._resolution.metadata.get("code", "")
        is_sent_from_cache = await self.send_video_from_cache(
            source_id=source_id,
            quality=quality,
        )
        if is_sent_from_cache:
            return

        video_dto = VideoDTO.from_savetik(
            savetikfh=data,
            source_id=source_id,
            resolution_url=self._resolution.url,
        )
        await self._send_video(video_dto)

    async def download_video(self) -> None:
        """Download video from Douyin using savetik.co API."""
        url_code = str(self._resolution.metadata.get("code"))
        self._process_percent(16)
        if await self.send_video_from_cache(
            source_id=url_code,
            quality="best",
        ):
            return
        try:
            info_dict = await self.get_video_info(url=self._resolution.url)
            info = SaveTikResponse.model_validate(info_dict)
            if not info:
                await self._send_error_message()
                return

            data = self._parse_web_data(info.data or "")
            if data is None:
                return
            self._process_percent(72)
            await self._handle_video(data=data)
        except Exception as e:
            logging.exception("Error in Douyin download process: %s", e)
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_error_downloading(
                telegram_id=self._telegram_id,
                resolution_url=self._resolution.url,
            )
