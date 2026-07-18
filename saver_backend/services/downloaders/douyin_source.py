import hashlib
import logging
import re
import time
from typing import Any, ClassVar

from httpx import AsyncClient, RequestError
from lxml.html import fromstring

from saver_backend.entities.enums import FsmKeysEnum, ProxyType, SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import (
    SeekinAiFromJson,
    SeekinAiResponse,
    VideoDTO,
)


class DouyinController(BaseSourceController):
    """Controller for downloading videos from douyin.com via seekin.ai."""

    SOURCE = SourceEnum.DOUYIN
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.LOCAL

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)
        self._video: VideoDTO | None = None
        self._api_url = "https://api.seekin.ai"
        self._web_url = "https://www.seekin.ai"
        self._client = AsyncClient(proxy=self._proxy)
        self._headers: dict[str, Any] = {
            "Lang": "en",
            "Sign": "",
            "Timestamp": "",
            "Sec-ch-ua": '"Google Chrome";v="149", '
            '"Chromium";v="149", "Not)A;Brand";v="24"',
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            " AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/149.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Host": "api.seekin.ai",
            "Accept-Encoding": "gzip",
        }

    def _get_signature(self, timestamp: str, url: str, secret_key: str) -> str:
        str_for_sign = f"en{timestamp}{secret_key}url={url}"
        bytes_data = str_for_sign.encode("utf-8")
        signed_bytes = hashlib.sha256(bytes_data).digest()
        return "".join(f"{b:02x}" for b in signed_bytes)

    async def _parse_secret_key(self) -> str | None:
        headers: dict[str, str] = {
            "Sec-ch-ua": self._headers.get("Sec-ch-ua", ""),
            "User-Agent": self._headers.get("User-Agent", ""),
            "Cookie": "linkstarry_i18n=en",
        }
        html = await self._client.get(url=self._web_url, headers=headers)
        tree = fromstring(html.text)
        js_elements = tree.xpath("//link[starts-with(@href,'/_nuxt')]/@href")
        trg_js_url = self._web_url + js_elements[2]

        headers.update({"Referer": self._web_url})
        js = await self._client.get(url=trg_js_url, headers=headers)
        pattern = (
            r'const q=\[\s*["\']([^"\']+)["\'],\s*["\']ikool["\'],'
            r'\s*["\']media["\'],\s*["\']download["\']\s*\]'
        )
        match = re.search(pattern, js.text)
        return match.group(1) if match else None

    async def _get_secret_key(self) -> str | None:
        saved_secret = await self._telegram_bot_controller.get_fsm_data(
            chat_id=int(FsmKeysEnum.DOUYIN), user_id=int(FsmKeysEnum.DOUYIN)
        )
        if not saved_secret:
            return None
        secret_key = saved_secret.get("secret_key")

        if not secret_key:
            secret_key = await self._parse_secret_key()
            await self._telegram_bot_controller.set_fsm_data(
                chat_id=int(FsmKeysEnum.DOUYIN),
                user_id=int(FsmKeysEnum.DOUYIN),
                data={"secret_key": secret_key},
            )

        return secret_key

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Fetch video info from api.seekin.ai.

        This method acts as the info-gathering step for seekin.ai,
        returning the raw API response data for further processing.

        :param url: The Douyin video URL.
        :return: The 'data' part of the api.seekin.ai response or None on failure.
        """

        try:
            secret_key = await self._get_secret_key()
            for _ in range(1, 3):
                timestamp = str(int(time.time() * 1000))
                sign = self._get_signature(
                    timestamp, self._resolution.url, secret_key or ""
                )
                self._headers.update({"Sign": sign, "Timestamp": timestamp})
                response = await self._client.post(
                    url=self._api_url + "/ikool/media/download",
                    headers=self._headers,
                    json={"url": self._resolution.url},
                )
                info_json = response.json()
                if not info_json:
                    return None
                if info_json.get("code") == "0000" and info_json.get("data"):
                    return info_json

                logging.warning(
                    "seekin.ai returned an error: %s (URL: %s); retry: %s",
                    info_json.get("msg"),
                    self._resolution.url,
                    _,
                )
            return None
        except RequestError as e:
            logging.exception(e)
            return None

    async def _handle_video(self, data: SeekinAiFromJson) -> None:
        """
        Handle caching, downloading, and sending of a single video.

        :param data: The data to send.
        """
        if not data.medias:
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

        video_dto = VideoDTO.from_seekinai(
            seekinaifj=data,
            source_id=source_id,
            resolution_url=self._resolution.url,
        )
        await self._send_video(video_dto)

    async def download_video(self) -> None:
        """Download video from Douyin using seekin.ai API."""
        url_code = str(self._resolution.metadata.get("code"))
        if await self.send_video_from_cache(
            source_id=url_code,
            quality="best",
        ):
            return
        self._process_percent(16)
        try:
            info_dict = await self.get_video_info(url=self._resolution.url)
            if not info_dict:
                await self._send_error_message()
                return
            info = SeekinAiResponse.model_validate(info_dict)

            data = SeekinAiFromJson.model_validate(info.data)
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
