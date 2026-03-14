import http.cookiejar
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Optional

import httpx
from lxml import html as lxml_html
from lxml.html import HtmlElement

from saver_backend.db.models.cache_model import CacheModel
from saver_backend.entities.enums import ContentTypeEnum, ProxyType, SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.schema import CacheDTO, WallVideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController


class VKWallParser(YtDlpController):
    """Asynchronous controller for parsing videos from VK wall posts."""

    COOKIES: ClassVar[bool] = True
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.RU
    SOURCE: ClassVar[SourceEnum] = SourceEnum.VK_WALL_PARSER
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    # Regular expressions for parsing
    OWNER_REGEX: ClassVar[re.Pattern[str]] = re.compile(r"wall(-?\d+)_\d+")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the VK wall parser with an HTTP client."""
        super().__init__(*args, **kwargs)
        self._client: Optional[httpx.AsyncClient] = None
        self._owner_id: Optional[str] = None
        self._video_type: Optional[str] = None
        self._video_id: Optional[str] = None

    def _load_cookies(self) -> dict[str, str]:
        """
        Load cookies from cookiefile specified in _base_options.

        :return: Dictionary with cookies for vk.ru vk.com domains.
        """

        cookies_dict: dict[str, str] = {}

        # Get cookiefile path from _base_options
        cookiefile_path = self._base_options.get("cookiefile")
        if not cookiefile_path:
            logging.warning(f"No cookiefile found in _base_options for {self.SOURCE}")
            return cookies_dict

        try:
            # Load cookies from Mozilla format cookie file
            cookie_jar = http.cookiejar.MozillaCookieJar()
            cookie_jar.load(cookiefile_path, ignore_discard=True, ignore_expires=True)

            # Convert cookies to dictionary, filtering by domain
            for cookie in cookie_jar:
                # Check domain - vk.com and vk.ru domains
                if cookie.domain and (
                    ".vk.ru" in cookie.domain
                    or "vk.ru" in cookie.domain
                    or ".vk.com" in cookie.domain
                    or "vk.com" in cookie.domain
                ):
                    cookies_dict[cookie.name] = str(cookie.value)
                    logging.debug(
                        f"Loaded cookie: {cookie.name} for domain {cookie.domain}",
                    )

            if cookies_dict:
                logging.info(
                    f"Loaded {len(cookies_dict)} cookies for"
                    "VK domains from {cookiefile_path}",
                )
            else:
                logging.warning(f"No VK domain cookies found in {cookiefile_path}")

        except FileNotFoundError:
            logging.error(f"Cookie file not found: {cookiefile_path}")
        except PermissionError:
            logging.error(
                f"Permission denied when reading cookie file: {cookiefile_path}",
            )
        except Exception as e:
            logging.error(f"Error loading cookies from {cookiefile_path}: {e}")

        return cookies_dict

    async def _parse_owner_from_url(self) -> bool:
        """
        Extract owner ID from the wall URL.

        :return: True if owner was found, False otherwise.
        """
        match = self.OWNER_REGEX.search(self._resolution.url)
        if match:
            self._owner_id = match.group(1)
            return True
        return False

    async def _fetch_wall_page(self) -> Optional[str]:
        """
        Fetch the wall page content.

        :return: HTML content as string or None if failed.
        """

        client = None
        cookies_dict = {}
        if self.COOKIES:
            cookies_dict = self._load_cookies()

        try:
            client = httpx.AsyncClient(
                headers={
                    "User-Agent": self.USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                },
                cookies=cookies_dict if cookies_dict else None,
                timeout=30.0,
                follow_redirects=True,
                proxy=self._proxy,
            )
            response = await client.get(self._resolution.url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as e:
            logging.error(f"HTTP error while fetching wall page: {e}")
            if e.response.status_code == 404:
                await self._telegram_bot_controller.send_content_not_found_error(
                    telegram_id=self._telegram_id,
                )
            return None
        except Exception as e:
            logging.error(f"Failed to fetch wall page {self._resolution.url}: {e}")
            return None
        finally:
            if client:
                await client.aclose()

    def _parse_video_data(self, html_content: str) -> bool:
        """
        Parse video type and ID from HTML content.

        :param html_content: HTML content.
        :return: True if both type and ID were found, False otherwise.
        """
        try:
            tree = lxml_html.fromstring(html_content)

            # Пытаемся получить данные разными способами
            json_data = self._extract_json_from_data_exec(tree)

            if not json_data:
                json_data = self._extract_json_from_api_prefetch(tree)

            if not json_data:
                logging.warning(
                    f"No video data found in HTML for {self._resolution.url}",
                )
                return False

            # Извлекаем данные видео из JSON
            return self._extract_video_info_from_json(json_data)

        except Exception as e:
            logging.error(f"Error parsing video data: {e}")
            return False

    def _extract_json_from_data_exec(
        self,
        tree: HtmlElement,
    ) -> Optional[dict[str, Any]]:
        """
        Extract JSON from data-exec attribute of PostContentContainer div.

        :param tree:
        :return:
        """

        xpath_expr = (
            "//div[contains(@class, 'PostContentContainer__root')"
            " and contains(@class, 'PostContentContainer')"
            " and @data-exec]"
        )
        elements = tree.xpath(xpath_expr)

        if not elements:
            return None

        element = elements[0]
        data_exec = element.get("data-exec")

        if not data_exec:
            return None

        try:
            return json.loads(data_exec)
        except json.JSONDecodeError as e:
            logging.warning(f"Failed to parse JSON from data-exec: {e}")
            return None

    def _extract_json_from_api_prefetch(
        self,
        tree: HtmlElement,
    ) -> Optional[dict[str, Any]]:
        """
        Extract video data from apiPrefetchCache in script tags.

        :param tree:
        :return:
        """

        xpath_expr = (
            "//script[contains(text(), 'apiPrefetchCache')"
            " and contains(text(), 'owner_id')]"
        )
        script_elements = tree.xpath(xpath_expr)

        for script in script_elements:
            script_text = script.text
            if not script_text or "apiPrefetchCache" not in script_text:
                continue

            json_data = self._parse_api_prefetch_script(script_text)
            if json_data:
                return json_data

        return None

    def _parse_api_prefetch_script(self, script_text: str) -> Optional[dict[str, Any]]:
        """
        Parse a single script tag containing apiPrefetchCache.

        :param script_text:
        :return:
        """

        try:
            cache_pattern = r'apiPrefetchCache":(\[.*?\])\}\);'
            cache_match = re.search(cache_pattern, script_text, re.DOTALL)

            if not cache_match:
                return None

            cache_json_str = cache_match.group(1)
            cache_data = json.loads(cache_json_str)

            return self._extract_video_from_cache_data(cache_data)

        except Exception as e:
            logging.warning(f"Failed to parse apiPrefetchCache: {e}")
            return None

    def _extract_video_from_cache_data(
        self,
        cache_data: list[Any],
    ) -> Optional[dict[str, Any]]:
        """
        Extract video data from parsed cache data.

        :param cache_data:
        :return:
        """

        for item in cache_data:
            if not isinstance(item, dict) or item.get("method") != "wall.getById":
                continue

            response = item.get("response", {})
            items = response.get("items", [])

            if not items:
                continue

            post_data = items[0]
            attachments = post_data.get("attachments", [])

            for attachment in attachments:
                video_json = self._create_video_json_from_attachment(attachment)
                if video_json:
                    return video_json

        return None

    def _create_video_json_from_attachment(
        self,
        attachment: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """
        Create video JSON structure from an attachment.

        :param attachment:
        :return:
        """

        attachment_type = attachment.get("type")

        if attachment_type not in ("clip", "video"):
            return None

        media_data = attachment.get(attachment_type, {})
        if not media_data:
            return None

        video_type = self._determine_video_type(attachment_type, media_data)

        return {
            "PostContentContainer/init": {
                "item": {
                    "attachments": [
                        {
                            "video": {
                                "id": media_data.get("id"),
                                "type": video_type,
                            },
                        },
                    ],
                },
            },
        }

    def _determine_video_type(
        self,
        attachment_type: str,
        media_data: dict[str, Any],
    ) -> str:
        """
        Determine the video type based on attachment type and media data.

        :param attachment_type:
        :param media_data:
        :return: video type
        """

        if attachment_type == "clip" and media_data.get("type") == "short_video":
            return "short_video"
        return attachment_type

    def _extract_video_info_from_json(self, json_data: dict[str, Any]) -> bool:
        """
        Extract video ID and type from JSON structure.

        :param json_data:
        :return:
        """

        # Проверяем структуру JSON
        init_data = json_data.get("PostContentContainer/init")
        if not init_data:
            logging.warning("'PostContentContainer/init' not found in JSON")
            return False

        item_data = init_data.get("item")
        if not item_data:
            logging.warning("'item' not found in PostContentContainer/init")
            return False

        attachments = item_data.get("attachments")
        if (
            not attachments
            or not isinstance(attachments, list)
            or len(attachments) == 0
        ):
            logging.warning("No attachments found in item")
            return False

        # Берем первое вложение
        first_attachment = attachments[0]

        # Получаем video объект
        video_data = first_attachment.get("video")
        if not video_data:
            logging.warning("No 'video' found in first attachment")
            return False

        # Получаем ID видео
        video_id = video_data.get("id")
        if video_id is None:
            logging.warning("Video ID not found")
            return False

        # Получаем тип видео
        video_type = video_data.get("type", "video")

        # Преобразуем тип
        if video_type == "short_video":
            video_type = "clip"

        self._video_id = str(video_id)
        self._video_type = video_type

        logging.info(
            f"Successfully parsed video data: type="
            f"{self._video_type}, id={self._video_id}",
        )
        return True

    def _build_video_url(self) -> str:
        """
        Build the video URL based on parsed data.

        :return: Constructed video URL.
        """
        return f"https://vk.com/{self._video_type}{self._owner_id}_{self._video_id}"

    def _extract_wall_key(self, url: str) -> str:
        """Get key from wall URL."""

        match = re.search(r"(wall[-]?\d+_\d+)", url)
        return match.group(1) if match else url.split("/")[-1]

    async def _cache_wall_data(self, wall_data: WallVideoDTO) -> Optional[CacheModel]:
        """
        Сохранить результаты парсинга стены в кэш.

        :param wall_data: данные, полученные из парсинга
        :return: созданная запись кэша или None
        """
        # Проверить, есть ли уже в кэше (по wall_key как source_id)
        existing = await self._cache_dao.get_by_filters(
            source=self.SOURCE,
            source_id=wall_data.wall_key,  # используем wall_key как source_id
            quality="parsed",  # специальное значение для parsed данных
            content_type=ContentTypeEnum.WALL_DATA,
        )

        if existing:
            logging.info(f"Wall data already cached for {wall_data.wall_key}")
            return existing

        # Create DTO for cache
        cache_dto = CacheDTO(
            source=self.SOURCE,
            source_id=wall_data.wall_key,  # ключ - часть URL
            quality="parsed",  # качество для parsed данных
            meta_data=wall_data,  # WallVideoDTO наследуется от BaseContentDTO
            file_id="",  # пустой file_id для parsed данных
            file_unique_id="",  # пустой для parsed данных
        )

        # Сохранить в БД
        try:
            created = await self._cache_dao.create(cache_dto)
            logging.info(f"Cached wall data for {wall_data.wall_key}")
            return created
        except Exception as e:
            logging.error(f"Failed to cache wall data: {e}")
            return None

    async def get_resolution(self) -> Resolution | None:
        """Parse VK wall and download the video using appropriate controller."""

        # Get owner from URL
        if not await self._parse_owner_from_url():
            logging.error(f"Could not parse owner from URL: {self._resolution.url}")
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
            return None

        wall_key = self._extract_wall_key(self._resolution.url)

        # Check cache
        cached = await self._cache_dao.get_by_filters(
            source=self.SOURCE,
            source_id=wall_key,
            quality="parsed",
            content_type=ContentTypeEnum.WALL_DATA,
        )

        # Check cache validity (optional)
        if cached and cached.meta_data_dto and await self._is_cache_valid(cached):
            wall_data = cached.meta_data_dto

            # Type narrowing - check for WallVideoDTO
            if not isinstance(wall_data, WallVideoDTO):
                logging.warning(f"Unexpected DTO type in cache: {type(wall_data)}")
            else:
                logging.info(f"Using cached wall data for {wall_key}")

                # Use data from cache
                self._owner_id = wall_data.owner_id
                self._video_type = wall_data.video_type
                self._video_id = wall_data.video_id

                # Build URL video
                video_url = wall_data.video_url
                self._resolution.url = video_url
                self._resolution.source = self._get_source_by_type(wall_data.video_type)

                return self._resolution

        html_content = await self._fetch_wall_page()
        if not html_content:
            return None

        if not self._parse_video_data(html_content):
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
            return None

        # Save into cache after parsing
        wall_data = WallVideoDTO(
            owner_id=self._owner_id,
            video_type=self._video_type,
            video_id=self._video_id,
            wall_key=wall_key,
            source_id=wall_key,
            url=self._resolution.url,
            quality="parsed",
        )
        await self._cache_wall_data(wall_data)

        # Build URL video
        video_url = self._build_video_url()
        logging.info(f"Extracted video URL: {video_url}")

        self._resolution.url = video_url
        if self._video_type is None:
            logging.error("Video type is None after parsing")
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
            return None

        self._resolution.source = self._get_source_by_type(self._video_type)

        return self._resolution

    def _get_source_by_type(self, video_type: str) -> SourceEnum:
        """Get appropriate controller."""

        if video_type == "video":
            return SourceEnum.VK_VIDEO_YDL
        if video_type == "clip":
            return SourceEnum.VK_CLIPS_YDL
        return SourceEnum.UNSUPPORTED

    async def _is_cache_valid(self, cache_entry: CacheModel) -> bool:
        """Check if the data in the cache is up to date."""

        if not cache_entry.created_at:
            return False

        # Make both datetimes offset-naive for comparison
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        cache_naive = cache_entry.created_at.replace(tzinfo=None)

        # cache is valid for 1 day (configurable)
        cache_age = now_naive - cache_naive
        return cache_age < timedelta(days=1)

    async def get_video_info(self, url: str) -> None:
        """
        Stub for the parent method.

        :param url:
        :return:
        """
        return

    async def close(self) -> None:
        """
        Close the HTTP client.

        :return:
        """
        if self._client and not self._client.is_closed:
            await self._client.aclose()
