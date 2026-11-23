import asyncio
import hashlib
import hmac
import html
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any, ClassVar, Optional, Union
from urllib.parse import urlparse

import py_mini_racer
from httpx import AsyncClient, RequestError
from lxml.html import fromstring
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.schema import (
    SeekinAiFromJson,
    SeekinAiResponse,
    VideoDTO,
)
from saver_backend.services.downloaders.ydl_source import YtDlpController


class SeekinAiController(YtDlpController):
    """Controller for downloading videos from douyin.com via seekin.ai."""

    SOURCE = SourceEnum.DOUYIN
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.LOCAL

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)
        self._video: VideoDTO | None = None
        self._api_url = "https://download.seekin.ai"
        self._web_url = "https://www.seekin.ai"
        self._client = AsyncClient(proxy=self._proxy)
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        self._cookie_dir = base_dir / "cookies" / "seekinai"

        self._headers: dict[str, Any] = {
            "Lang": "en",
            "Sign": "",
            "Timestamp": "",
            "Sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24",'
            ' "Google Chrome";v="152"',
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            " AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/152.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
        }
        douyin_params = {
            "downloader": "aria2c",
            "external_downloader_args": {
                "aria2c": [
                    "-x",
                    "16",
                    "-s",
                    "16",
                    "-k",
                    "1M",
                    "--timeout=60",  # Таймаут для соединения
                    "--max-tries=10",  # Максимальное количество попыток
                    "--retry-wait=5",  # Ждать между попытками
                ],
            },
        }
        self._yt_dlp.params.update(douyin_params)

    def _generate_nonce(self) -> str:
        """Generate a random 16-byte hex string (nonce)."""
        return secrets.token_hex(16)

    def _get_signature(self, timestamp: str, url: str, secret_key: str) -> str:
        str_for_sign = f"en{timestamp}{secret_key}url={url}"
        bytes_data = str_for_sign.encode("utf-8")
        signed_bytes = hashlib.sha256(bytes_data).digest()
        return "".join(f"{b:02x}" for b in signed_bytes)

    def _get_secret_from_cookie(self) -> bytes | None:
        """Extract secret from cookie file."""
        cookie_files = list(self._cookie_dir.glob("cookies*.txt"))
        if not cookie_files:
            return None
        cookie_file = str(cookie_files[0])
        cookie_path = Path(cookie_file)
        try:
            with cookie_path.open("r", encoding="utf-8") as f:
                cookies = json.load(f)
                return bytes(cookies["secret"], "utf-8")
        except (json.JSONDecodeError, OSError) as e:
            logging.warning("[seekinai] Failed to load cookies: %s", e)
        return None

    def _save_secret_to_cookie(self, secret: bytes) -> None:
        """Save secret to cookie file."""
        cookie_files = list(self._cookie_dir.glob("cookies*.txt"))
        if cookie_files:
            cookie_path = cookie_files[0]
        else:
            cookie_path = self._cookie_dir / "cookies.txt"

        try:
            self._cookie_dir.mkdir(parents=True, exist_ok=True)
            with cookie_path.open("w", encoding="utf-8") as f:
                json.dump({"secret": secret.decode("utf-8")}, f)
        except OSError as e:
            logging.exception("[seekinai] Failed to save cookies: %s", e)

    def _calculate_secret_key(self, jstext: str) -> bytes | None:
        secret = self._get_secret_from_cookie()
        if secret:
            return secret
        pattern = r"\}(const [a-zA-Z]{2,}=\[([\d,]{23,130})\].{10,400}\})function"
        match = re.search(pattern, jstext, re.DOTALL)
        if match:
            js_code = match.group(1)
            ctx = py_mini_racer.MiniRacer()
            ctx.eval(js_code)
            funcs = ctx.eval("JSON.stringify(Object.keys(globalThis))")
            available_funcs = json.loads(funcs)
            if not available_funcs:
                return None
            result = ctx.eval(available_funcs[0] + "();")
            secret = bytes(result)
            self._save_secret_to_cookie(secret)
            return bytes(secret)

        return None

    async def _get_signature_key(self, timestamp: int) -> bytes:

        secret_key = await self._parse_secret_key()
        version_str = f"v2|{timestamp // 10000}"

        return self._hmac_hash(secret_key or b"", version_str)

    async def _parse_secret_key(self) -> bytes | None:
        headers: dict[str, str] = {
            "Sec-ch-ua": self._headers.get("Sec-ch-ua", ""),
            "User-Agent": self._headers.get("User-Agent", ""),
            "Cookie": "linkstarry_i18n=en",
        }
        html = await self._client.get(url=self._web_url, headers=headers, timeout=30)
        tree = fromstring(html.text)
        js_elements = tree.xpath("//astro-island[@component-url]/@component-url")
        trg_js_url = self._web_url + js_elements[0]

        headers.update({"Referer": self._web_url})
        js = await self._client.get(url=trg_js_url, headers=headers)
        return self._calculate_secret_key(js.text)

    def _hash_data(self, data: Union[str, bytes, dict[str, Any], None]) -> str:
        """Generate SHA-256 hash of data and return as hex string."""
        if data is None:
            return ""

        # Convert data to bytes
        if isinstance(data, str):
            content = data.encode("utf-8")
        elif isinstance(data, bytes):
            content = data
        elif isinstance(data, dict):
            content = json.dumps(data, separators=(",", ":")).encode("utf-8")
        else:
            content = str(data).encode("utf-8")

        if len(content) == 0:
            return ""

        # Compute SHA-256 hash
        hash_obj = hashlib.sha256(content)
        return hash_obj.hexdigest()

    def _hmac_hash(self, key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    async def _sign_request_data(
        self,
        url: str,
        method: str = "POST",
        timestamp: Optional[int] = None,
        nonce: Optional[str] = None,
        lang: Optional[str] = None,
    ) -> dict[str, str]:
        """Generate signature headers for a request."""

        parsed_url = url.split("?")
        path = "/ikool/media/download"
        query = ""
        if len(parsed_url) > 1:
            query = parsed_url[1]

        # Get query string without the '?' prefix
        query_string = query

        # Get timestamp
        if timestamp is None:
            timestamp = int(time.time() * 1000)

        # Get nonce
        if nonce is None:
            nonce = self._generate_nonce()

        # Get language
        if lang is None:
            lang = "en"
        # Build the message for signature
        # Format: method\npath\nquery\nbody_hash\n timestamp\nnonce\nlang
        # For body hash, we use the raw body bytes
        body = {"url": url}
        body_hash = self._hash_data(data=body)

        message = (
            f"{method}\n{path}\n{query_string}"
            f"\n{body_hash}\n{timestamp}\n{nonce}\n{lang}"
        )

        # Generate signature key from timestamp
        signature_key = await self._get_signature_key(timestamp)

        # Generate HMAC signature (using SHA-256)
        signature = self._hmac_hash(signature_key, message)

        return {
            "Lang": lang,
            "Timestamp": str(timestamp),
            "Nonce": nonce,
            "Sign": signature.hex(),
        }

    def _process_media_sizes(self, info_dict: dict[str, Any]) -> dict[str, Any]:
        """
        Processes the medias list by adding a 'size' field.

        Removes elements with size in GB.

        :param info_dict: dictionary with data
        :return: modified info_dict
        """

        if "data" not in info_dict or "medias" not in info_dict["data"]:
            return info_dict

        medias = info_dict["data"]["medias"]
        filtered_medias = []

        for media in medias:
            if media.get("format") is None:
                continue

            try:
                # Extract size from parentheses
                size_str = media["format"].split("(")[1].split(")")[0]

                # Check if size contains GB
                if "GB" in size_str:
                    continue  # Skip elements with GB

                # Add size field
                media["size"] = size_str
                filtered_medias.append(media)

            except IndexError, AttributeError:
                # If size extraction fails, skip the element
                continue

        # Update the medias list
        if filtered_medias:
            info_dict["data"]["medias"] = filtered_medias

        return info_dict

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
            lang, timestamp, nonce, sign = (
                await self._sign_request_data(url=url)
            ).values()
            for _ in range(1, 3):
                self._headers.update(
                    {
                        "Lang": lang,
                        "Nonce": nonce,
                        "Sign": sign,
                        "Timestamp": timestamp,
                    }
                )
                response = await self._client.post(
                    url=self._api_url + "/ikool/media/download",
                    headers=self._headers,
                    json={"url": self._resolution.url},
                    timeout=30,
                )
                info_json = response.json()
                if not info_json:
                    return None
                if info_json.get("code") == "0000" and info_json.get("data"):
                    return self._process_media_sizes(info_json)

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
        video_dto = await self._download_video_by_url(data.medias[0].url)
        if video_dto is None:
            for fallback_url in await self._fallback_media_urls(data.medias[0].url):
                video_dto = await self._download_video_by_url(fallback_url)
                if video_dto is not None:
                    break
        if video_dto is None:
            # the user was left with a progress message that never finished
            await self._send_error_message()
            return
        video_dto = VideoDTO.from_seekinai(
            video_dto=video_dto,
            seekinaifj=data,
            source_id=source_id,
            resolution_url=self._resolution.url,
        )
        await self._send_video(video_dto)

    async def _fallback_media_urls(self, failed_url: str) -> list[str]:
        """
        Other links to the same file when the one from seekin.ai fails.

        :param failed_url: The file link from seekin.ai that did not download.
        :return: Links to try next, none by default.
        """
        return []

    async def _download_video_by_url(
        self,
        video_url: str,
    ) -> Optional[VideoDTO]:
        """
        Download video using yt-dlp.

        Args:
            video_url: Direct video URL from the video tracks

        Returns:
            VideoDTO if successful, None otherwise
        """

        # Download via yt-dlp

        ext = "mp4"
        logging.info("Downloading video: %s", video_url)
        self._yt_dlp.params["outtmpl"].update(
            {
                "default": str(
                    self._download_directory / f"{self._resolution.metadata['code']}"
                    f".{self._download_token}.%(ext)s",
                ),
            }
        )
        try:
            info_dict = await asyncio.to_thread(
                self._yt_dlp.extract_info,
                url=video_url.strip(),
                download=True,
            )

            # downloaded video path
            predicted_path = (
                self._download_directory / f"{self._resolution.metadata['code']}"
                f".{self._download_token}.{info_dict['ext']}"
            )

            return VideoDTO.from_yt_dlp(
                info=info_dict,
                file_path=predicted_path,
                quality=ext,
            )

        except DownloadError as e:
            logging.error("[douyin] Failed to download video %s: %s", video_url, e)
        except Exception as e:
            logging.exception(
                "[douyin] Unexpected error downloading video %s: %s",
                video_url,
                e,
            )
        return None

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
                await self._send_error_message()
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


def create_seekin_controller(source: SourceEnum) -> type[SeekinAiController]:
    """Factory for creating SeekIn-based controllers."""

    class _SeekInController(SeekinAiController):
        SOURCE = source

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
            self._download_directory.mkdir(parents=True, exist_ok=True)

    return _SeekInController


KWAI_PAGE_MP4_RE = re.compile(r"https://[^\"'\s<>]+?\.mp4(?:\?[^\"'\s<>]*)?")
KWAI_FALLBACK_LIMIT = 3


def kwai_file_name(url: str) -> str:
    """File name of a Kwai video link, the same on every CDN host."""
    return urlparse(url).path.rsplit("/", 1)[-1]


def pick_kwai_page_urls(page: str, failed_url: str) -> list[str]:
    """
    Links from the Kwai page to the same file as the one that failed.

    Prod 23.11: seekin.ai gave a txmov2.a.kwimgs.com link that answered 404,
    while the video page links the same file on ak-br-cdn.kwai.net and it
    downloads. The page also links recommended videos, so only links with the
    same file name are taken.

    :param page: HTML of the Kwai video page.
    :param failed_url: The file link from seekin.ai.
    :return: Up to KWAI_FALLBACK_LIMIT other links to the same file.
    """
    name = kwai_file_name(failed_url)
    if not name:
        return []
    text = html.unescape(page.replace("\\u002F", "/").replace("\\u0026", "&"))
    urls: list[str] = []
    for url in KWAI_PAGE_MP4_RE.findall(text):
        if url != failed_url and kwai_file_name(url) == name and url not in urls:
            urls.append(url)
    return urls[:KWAI_FALLBACK_LIMIT]


class KwaiController(SeekinAiController):
    """Kwai via seekin.ai, with the video page as a second source of the file."""

    SOURCE = SourceEnum.KWAI

    async def _fallback_media_urls(self, failed_url: str) -> list[str]:
        try:
            response = await self._client.get(
                self._resolution.url,
                headers={"User-Agent": self._headers["User-Agent"]},
                follow_redirects=True,
                timeout=30,
            )
        except RequestError as e:
            logging.warning("Kwai page did not open: %s", e)
            return []
        urls = pick_kwai_page_urls(response.text, failed_url)
        logging.info("Kwai page gave %s other links to the file", len(urls))
        return urls


KuaishouController = create_seekin_controller(SourceEnum.KUAISHOU)
DouyinController = create_seekin_controller(SourceEnum.DOUYIN)
