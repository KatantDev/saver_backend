import asyncio
import logging
import re
import uuid
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

import ffmpeg
import httpx

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import (
    PhotoDTO,
    VideoDTO,
)


class InstagramInDownController(BaseSourceController):
    """
    Controller for downloading Instagram content via indown.io scraping.

    Replaces yt-dlp and instaloader approaches.
    """

    SOURCE: ClassVar[SourceEnum] = SourceEnum.INSTAGRAM_INDOWN  # Keep generic enum
    BASE_URL = "https://indown.io"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)

        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Origin": self.BASE_URL,
            },
            timeout=30,
            follow_redirects=True,
            proxy=self._proxy,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    def _normalize_url(self, url: str) -> str:
        """
        Fixes Instagram URLs for indown.io.

        Known issue: indown fails with /reels/ (plural), needs /reel/ (singular).
        """
        if "/reels/" in url:
            return url.replace("/reels/", "/reel/")
        return url

    async def _get_csrf_token(self, referer: str) -> str | None:
        """Fetch the page to get the valid CSRF token."""
        try:
            response = await self._client.get(referer)
            response.raise_for_status()
            match = re.search(r'name="_token" value="([^"]+)"', response.text)
            if match:
                return match.group(1)
        except Exception as e:
            logging.error(f"Failed to get CSRF token from {referer}: {e}")
        return None

    def _extract_clean_url(self, raw_url: str) -> str | None:
        """
        Extracts the real CDN URL.

        Handles both direct links and indown.io/fetch proxy links.
        """
        # Clean HTML entities
        url = raw_url.replace("&amp;", "&")

        if "indown.io/fetch" in url:
            try:
                parsed = urlparse(url)
                qs = parse_qs(parsed.query)
                if "url" in qs:
                    return qs["url"][0]
            except Exception:
                logging.warning(f"Failed to parse proxy URL: {url}")
                return None
            return None
        return url

    def _extract_source_id(self, url: str) -> str:
        """Extract generic ID from URL for cache keys."""
        path = urlparse(url).path.rstrip("/")
        return path.split("/")[-1]

    async def _download_file(self, url: str, filename: str) -> None:
        """
        Download file from URL to local storage.

        :param url: Direct URL to file.
        :param filename: Target filename.
        """
        file_path = self._download_directory / filename
        try:
            async with self._client.stream("GET", url) as response:
                response.raise_for_status()
                # Используем стандартный open для записи в чанках,
                # чтобы не загружать память для больших видео.
                # Блокировка IO минимальна при записи чанками.
                with file_path.open("wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
        except Exception as e:
            logging.error(f"Failed to download file {url}: {e}")
            if file_path.exists():
                file_path.unlink()
            raise

    async def download_video(self) -> None:
        """Main execution flow."""
        original_url = self._resolution.url
        source_id = self._extract_source_id(original_url)

        # 1. Check Cache
        if await self.send_video_from_cache(source_id=source_id, quality="best"):
            return

        normalized_url = self._normalize_url(original_url)
        referer_path = "/en1"
        referer_url = f"{self.BASE_URL}{referer_path}"

        # 2. Get Token
        token = await self._get_csrf_token(referer_url)
        if not token:
            logging.error("Could not fetch CSRF token from indown.io")
            await self._send_error_message()
            return

        # 3. Update headers with Referer for the POST request
        self._client.headers.update({"Referer": referer_url})

        # 4. Post Data
        payload = {
            "link": normalized_url,
            "referer": referer_url,
            "locale": "en",
            "_token": token,
        }

        try:
            self._process_percent(10)
            response = await self._client.post(
                f"{self.BASE_URL}/download",
                data=payload,
            )
            response.raise_for_status()
            html = response.text
            self._process_percent(40)
        except Exception as e:
            logging.exception(f"Failed to fetch data from indown.io: {e}")
            await self._send_error_message()
            return

        if "Not Found" in html or "Private Video" in html:
            await self._telegram_bot_controller.send_content_not_found_error(
                self._telegram_id,
            )
            return

        # 5. Parse and Send
        media_items = self._extract_media_dtos(html, original_url, source_id)
        await self._send_media_result(media_items)

    def _create_dto(
        self,
        clean_url: str,
        original_url: str,
        source_id: str,
    ) -> VideoDTO | PhotoDTO:
        """Factory method to create DTO based on file extension or url pattern."""
        is_video = False
        parsed_url = urlparse(clean_url)
        host = (parsed_url.hostname or "").lower()
        # Check the host as a structured suffix (host == googlevideo.com or
        # *.googlevideo.com) instead of an unanchored substring search,
        # otherwise URLs like https://evil.com/?fake=googlevideo.com would
        # incorrectly be treated as videos. The .mp4 check is also anchored
        # on the URL path to ignore query/fragment occurrences.
        if (
            parsed_url.path.endswith(".mp4")
            or host == "googlevideo.com"
            or host.endswith(".googlevideo.com")
        ):
            is_video = True
        elif (
            ".jpg" not in clean_url
            and ".jpeg" not in clean_url
            and ".webp" not in clean_url
        ):
            # Если в ссылке есть 'dst-jpg' или 'dst-webp' - это фото.
            if "dst-jpg" in clean_url or "dst-webp" in clean_url:
                is_video = False
            else:
                # Fallback: считаем видео, если не доказано обратное
                is_video = True

        if is_video:
            return VideoDTO(
                url=original_url,
                source_id=source_id,
                direct_download_url=clean_url,
                quality="best",
            )
        return PhotoDTO(
            url=original_url,
            source_id=source_id,
            media_url=clean_url,
        )

    def _extract_carousel_links(self, html: str) -> list[str]:
        """Extract links from button groups (used for carousels)."""
        button_groups = re.findall(
            r'<div class="btn-group-vertical">([\s\S]*?)</div>',
            html,
        )
        found_urls: list[str] = []

        for group_html in button_groups:
            # Внутри группы ищем все ссылки (обычно Server 1 и Server 2)
            # Нам достаточно первой рабочей ссылки из группы
            hrefs = re.findall(r'href="([^"]+)"', group_html)

            for href in hrefs:
                clean = self._extract_clean_url(href)
                # Пропускаем пустые или уже добавленные ссылки
                if clean and clean not in found_urls:
                    found_urls.append(clean)
                    break

        return found_urls

    def _extract_media_dtos(
        self,
        html: str,
        original_url: str,
        source_id: str,
    ) -> list[PhotoDTO | VideoDTO]:
        """Main parsing logic converting HTML to DTOs."""
        clean_links = self._extract_carousel_links(html)

        media_items = []
        for link in clean_links:
            dto = self._create_dto(link, original_url, source_id)
            media_items.append(dto)

        return media_items

    def _fill_video_metadata(self, video_dto: VideoDTO) -> None:
        """Extract video metadata using ffmpeg."""
        if not video_dto.path:
            return

        file_path = str(video_dto.path)

        try:
            # 1. Probe video details
            probe = ffmpeg.probe(file_path)
            video_stream = next(
                (
                    stream
                    for stream in probe["streams"]
                    if stream["codec_type"] == "video"
                ),
                None,
            )
            if video_stream:
                video_dto.width = int(video_stream["width"])
                video_dto.height = int(video_stream["height"])

                # Duration can be in format or stream
                duration = probe["format"].get("duration") or video_stream.get(
                    "duration",
                )
                if duration:
                    video_dto.duration = int(float(duration))

            # 2. Generate thumbnail if we found video stream
            if video_stream:
                thumb_path = file_path.rsplit(".", 1)[0] + ".jpg"
                (
                    ffmpeg.input(file_path, ss=0.1)  # Grab frame at 0.1s
                    .filter("scale", 320, -1)  # Resize to reasonable thumb width
                    .output(thumb_path, vframes=1)
                    .overwrite_output()
                    .run(quiet=True, capture_stdout=True, capture_stderr=True)
                )
                video_dto.thumbnail = thumb_path

        except ffmpeg.Error as e:
            logging.error(f"FFmpeg error for {file_path}: {e.stderr.decode('utf8')}")
        except Exception as e:
            logging.error(f"Failed to extract metadata for {file_path}: {e}")

    async def _process_single_item(
        self,
        item: PhotoDTO | VideoDTO,
        index: int,
    ) -> None:
        """Process a single media item: download and extract metadata."""
        url = None
        ext = ""

        if isinstance(item, VideoDTO):
            url = item.direct_download_url
            ext = "mp4"
        elif isinstance(item, PhotoDTO):
            url = item.media_url
            ext = "jpg"

        if not url:
            return

        # Unique filename
        filename = f"{item.source_id}_{index}_{uuid.uuid4().hex[:6]}.{ext}"
        local_path = self._download_directory / filename

        await self._download_file(url, filename)

        # Update DTO: set local path
        item.path = local_path

        if isinstance(item, VideoDTO):
            item.direct_download_url = None
            await asyncio.to_thread(self._fill_video_metadata, item)
        elif isinstance(item, PhotoDTO):
            item.media_url = None

    async def _process_and_download_items(
        self,
        media_items: list[PhotoDTO | VideoDTO],
    ) -> None:
        """
        Download list of media items to local disk.

        Updating DTOs with local paths and enriching video metadata via ffmpeg.
        """
        for index, item in enumerate(media_items):
            try:
                await self._process_single_item(item, index)
            except Exception as e:
                logging.error(f"Failed to download/process item {index}: {e}")

    async def _send_media_result(
        self,
        media_items: list[PhotoDTO | VideoDTO],
    ) -> None:
        """Sends the resulting DTOs to Telegram."""
        if not media_items:
            logging.warning("No media found in indown.io response.")
            await self._send_error_message()
            return

        self._process_percent(60)

        # Download files locally because Telegram might reject direct links
        # due to 403 Forbidden or IP restrictions
        await self._process_and_download_items(media_items)

        self._process_percent(80)

        if len(media_items) == 1:
            item = media_items[0]
            if isinstance(item, VideoDTO):
                await self._send_video(item)
            elif isinstance(item, PhotoDTO):
                await self._telegram_bot_controller.send_finish_downloading_photo(
                    photo=item,
                    telegram_id=self._telegram_id,
                    message_id=self._message_id,
                )
                await self._create_history_entry()
        else:
            # Carousel / Album
            await self._telegram_bot_controller.send_finish_downloading_group(
                files=media_items,
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )
            await self._create_history_entry()

        self.cleanup_files(media_items)
