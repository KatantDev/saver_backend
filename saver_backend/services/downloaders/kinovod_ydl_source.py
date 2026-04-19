import asyncio
import json
import logging
import re
import secrets
import socket
from collections import deque
from typing import Any, ClassVar, Optional

import httpx
import slippers
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    ProxySettings,
    Route,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from yt_dlp import DownloadError

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.downloaders.exceptions import KinovodCaptchaError
from saver_backend.services.downloaders.schema import VideoDTO, VideoTheatreDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController
from saver_backend.settings import settings
from saver_backend.telegram_bot.keyboards.callback import VideoTranslationCallback


class KinovodYdlController(YtDlpController):
    """
    Controller for Kinovod.pro video downloads.

    Handles:
    - kinovod.pro/film/XXXXX (Film pages)
    - Extracts video URL from video tag after page loads
    - Downloads via yt-dlp with proxy support
    """

    SOURCE: ClassVar[SourceEnum] = SourceEnum.KINOVOD_YDL
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.LOCAL
    COOKIES: ClassVar[bool] = False

    # Selectors
    ALERT_SELECTOR: ClassVar[str] = "//div[@class='alert']"
    VIDEO_SELECTOR: ClassVar[str] = "//video[@src]"

    # Timeout configurations
    PAGE_LOAD_TIMEOUT: ClassVar[int] = 30000  # 30 seconds
    ELEMENT_CHECK_INTERVAL: ClassVar[int] = 1000  # 1 second
    MAX_WAIT_TIME: ClassVar[int] = 60000  # 60 seconds

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        # Configure yt-dlp for optimal video downloading

        kinovod_params = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
            "format": "all",
        }
        self._yt_dlp.params.update(kinovod_params)

        # Store browser and page for cleanup
        self._browser: Optional[Browser] = None
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._source_id: Optional[str] = None
        self._title: Optional[str] = None
        self._proxy_local: Optional[slippers.Proxy] = None
        self._proxies_rotate: deque[str] = deque(self._proxies)
        self._perevod_from_html: Optional[str] = None
        self._thumbnail_url: Optional[str] = None
        self._translation_names: dict[str, Any] = {}

    async def close(self) -> None:
        """Close browser and page resources."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        await super().close()

    async def _load_film(self, url: str) -> None:
        """
        Load the film page and wait for initial load.

        Args:
            url: Kinovod film URL
        """
        logging.info("Loading film page: %s", url)
        if self._page is None:
            return

        # Navigate to the page
        await self._page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self.PAGE_LOAD_TIMEOUT,
        )

    async def _parse_perevod(self) -> Optional[str]:
        """
        Extracts translation information from the page.

        Returns:
            Translation text or None if not found
        """
        try:
            if self._page is None:
                return None
            # Wait for the translation element
            perevod_element = await self._page.wait_for_selector(
                "//div[.='Перевод']/following-sibling::div",
                timeout=5000,  # 5 seconds timeout
            )

            if perevod_element:
                perevod_text = await perevod_element.text_content()
                if perevod_text:
                    logging.info(f"Found translation: {perevod_text}")
                    return perevod_text.strip()

            logging.warning(
                "No translation element found with "
                "selector //div[.='Перевод']/following-sibling::div",
            )
            return None

        except PlaywrightTimeoutError:
            logging.debug("Timeout waiting for translation element")
            return None
        except Exception as e:
            logging.error(f"Error getting translation: {e}")
            return None

    async def _check_load(self) -> Optional[str]:  # noqa: C901
        """
        Check for alert or video element.

        Returns:
            Direct video URL if video found, None if alert found or timeout

        Raises:
            Exception: If alert message is found
        """
        start_time = asyncio.get_event_loop().time()
        if self._page is None:
            return None
        while True:
            elapsed = (asyncio.get_event_loop().time() - start_time) * 1000
            if elapsed > self.MAX_WAIT_TIME:
                logging.error(
                    "Timeout waiting for video or alert after %d ms",  # todo reload
                    self.MAX_WAIT_TIME,
                )
                return None

            # Check for alert
            try:
                alert_element = await self._page.wait_for_selector(
                    self.ALERT_SELECTOR,
                    timeout=self.ELEMENT_CHECK_INTERVAL,
                )
                if alert_element:
                    alert_text = await alert_element.text_content()
                    logging.error("Alert found on page: %s", alert_text)
                    raise Exception(f"Site error: {alert_text}")
                # Check for captcha
                captcha_element = await self._page.wait_for_selector(
                    '//img[@id="captcha_image"]',
                    timeout=self.ELEMENT_CHECK_INTERVAL,
                )
                if captcha_element:
                    logging.error("Captcha found on page")
                    raise KinovodCaptchaError
            except PlaywrightTimeoutError:
                pass  # No alert found, continue

            # Check for video element with src
            try:
                video_element = await self._page.wait_for_selector(
                    self.VIDEO_SELECTOR,
                    timeout=self.ELEMENT_CHECK_INTERVAL,
                )
                if video_element:
                    video_src = await video_element.get_attribute("src")
                    if video_src:
                        h1_element = await self._page.wait_for_selector(
                            "//h1",
                            timeout=self.ELEMENT_CHECK_INTERVAL,
                        )
                        if h1_element:
                            self._title = await h1_element.text_content()
                        if video_element:
                            video_src = await video_element.get_attribute("src")
                        logging.info(f"Found video '{self._title}' source: {video_src}")
                        return video_src
            except PlaywrightTimeoutError:
                pass  # No video found yet

            # Wait before next check
            await asyncio.sleep(self.ELEMENT_CHECK_INTERVAL / 1000)

    async def _get_thumb(self) -> Optional[str]:
        """
        Extracts thumbnail URL from the page.

        Looks for element .poster > img and gets its src attribute.

        Returns:
            Thumbnail URL or None if not found
        """
        try:
            # Wait for poster image
            if self._page is None:
                return None
            poster_img = await self._page.query_selector(".poster > img")

            if poster_img:
                thumbnail_url = await poster_img.get_attribute("src")
                if thumbnail_url:
                    logging.info(f"Found thumbnail: {thumbnail_url}")
                    return "https://kinovod.pro" + thumbnail_url

            logging.warning("No thumbnail found with selector .poster > img")
            return None

        except Exception as e:
            logging.error(f"Error getting thumbnail: {e}")
            return None

    async def _download_video_by_urls(
        self,
        video_urls: list[str],
    ) -> Optional[VideoDTO]:
        """
        Download video using yt-dlp.

        Args:
            video_urls: Direct video URLs from the info_dict

        Returns:
            VideoDTO if successful, None otherwise
        """

        # Download via yt-dlp
        for video_url in video_urls:
            ext = video_url.split(".")[-1]
            logging.info("Downloading video: %s", video_url)
            try:
                info_dict = await asyncio.to_thread(
                    self._yt_dlp.extract_info,
                    url=video_url.strip(),
                    download=True,
                )

                # downloaded video path
                predicted_path = self._download_directory / f"{info_dict['id']}.{ext}"

                return VideoDTO.from_yt_dlp(
                    info=info_dict,
                    file_path=predicted_path,
                    quality=ext,
                )

            except DownloadError as e:
                logging.error("Failed to download video %s: %s", video_url, e)
            except Exception as e:
                logging.exception(
                    "Unexpected error downloading video %s: %s",
                    video_url,
                    e,
                )
        return None

    async def _is_port_free(
        self,
        port: int,
        host: str = settings.taskiq_worker_host,
        retries: int = 1,
    ) -> bool:
        """
        Check if a port is free on the given host.

        Args:
            port: Port number to check
            host: Hostname or IP address

        Returns:
            True if port is free, False otherwise
        """
        for _ in range(1, retries + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind((host, port))
                    if retries > 1:
                        await asyncio.sleep(0.5)
                        continue
                    return True
                except socket.error:
                    return False
        return False

    async def _prepare_proxy(self, url: str, timeout: int = 5) -> bool:
        """
        Search working proxy.

        :param url: URL to test the proxy against.
        :param timeout: Timeout in seconds.
        :return: True if proxy works, False otherwise.
        """
        if not self._proxy:
            return False

        while True:
            try:
                async with httpx.AsyncClient(
                    proxy=self._proxy,
                    timeout=timeout,
                ) as client:
                    response = await client.get(url)
                    return response.status_code < 500
            except Exception:
                logging.warning(f"Bad proxy {self._proxy}")
                if len(self._proxies_rotate) > 0:
                    self._proxies_rotate.rotate(-1)
                    self._proxy = self._proxies_rotate[0]
                    self._yt_dlp.params.update({"proxy": self._proxy})
                return False

    async def _check_proxy(
        self,
        proxy: str,
        url: str = settings.chrome_cdp_url,
        timeout: int = 15,
    ) -> bool:
        """
        Check if proxy is working.

        :param url: URL to test the proxy against.
        :param timeout: Timeout in seconds.
        :return: True if proxy works, False otherwise.
        """

        try:
            async with httpx.AsyncClient(
                proxy=proxy,
                timeout=timeout,
            ) as client:
                response = await client.get(url)
                return response.status_code < 500
        except Exception:
            logging.warning(f"Bad local proxy {proxy}")
            return False

    async def _find_free_port(
        self,
        start_port: int = 31080,
        end_port: int = 31200,
    ) -> int:
        """
        Find a free port in the specified range.

        Args:
            start_port: Starting port number (inclusive)
            end_port: Ending port number (inclusive)

        Returns:
            Free port number

        Raises:
            RuntimeError: If no free port found in the range
        """
        for _p in range(start_port, end_port + 1):
            port = secrets.choice(range(start_port, end_port))
            if await self._is_port_free(port):
                logging.info("Found free port: %d", port)
                return port
        raise RuntimeError(f"No free port found in range {start_port}-{end_port}")

    async def _raise_proxy(self, port: int) -> None:
        """
        Create and start a local SOCKS5 proxy passthrough for authenticated upstream.

        The proxy is stored in self._proxy_local and automatically starts in background.
        Upstream proxy URL is taken from settings._proxy

        Args:
            port: Local port to bind the proxy to

        Raises:
            RuntimeError: when local proxy is failed to start
        """
        if not self._proxy:
            return
        await self._prepare_proxy(self._resolution.url)
        upstream_proxy_url = self._proxy

        logging.info("Starting slippers proxy on :%d -> %s", port, upstream_proxy_url)
        self._proxy_local = slippers.Proxy(
            upstream_proxy_url,
            host=settings.taskiq_worker_host,
            port=port,
        )
        self._proxy_local.start()
        await asyncio.sleep(3)
        # Verify proxy is actually running
        if await self._check_proxy(f"socks5://{settings.taskiq_worker_host}:{port}"):
            logging.info("Slippers proxy successfully started on port %d", port)
            return
        raise RuntimeError(f"Failed to start slippers proxy on port {port}")

    async def start_cdp(self) -> None:
        """
        Start cdp session.

        :return:
        """
        # Initialize Playwright with proxy support
        playwright = await async_playwright().start()
        self._playwright = playwright

        # Find free port for slippers proxy
        local_proxy_port = await self._find_free_port()

        # Start slippers proxy
        await self._raise_proxy(local_proxy_port)

        # Get Chrome CDP URL from settings
        chrome_cdp_url = settings.chrome_cdp_url

        logging.info("Connecting... to Chrome CDP: %s", chrome_cdp_url)

        # Connect to existing Chrome container
        browser = await playwright.chromium.connect_over_cdp(chrome_cdp_url)
        self._browser = browser

        # Create context with proxy (per-context proxy overrides global if set) #
        proxy_settings = ProxySettings(
            server=f"socks5://{settings.taskiq_worker_host}:{local_proxy_port}",
        )
        self._context = await browser.new_context(
            ignore_https_errors=True,
            proxy=proxy_settings,
        )

        self._page = await self._context.new_page()

    async def _change_playerjs(self, route: Route) -> None:
        """Intercepts requests to playerjs.js and modifies the response body."""
        try:
            response = await route.fetch()
            body = await response.text()

            # Modify JavaScript code
            modified_body = body.replace(
                "function Playerjs(options){",
                "function Playerjs(options){RTCCertificate.plstdic = options.file;",
            )

            await route.fulfill(
                response=response,
                body=modified_body,
            )
        except Exception as e:
            logging.error(f"Error in _change_playerjs: {e}")
            await route.continue_()

    async def _get_playlist_dict(self) -> str:
        """
        Executes JavaScript to get the playlist dictionary.

        Returns:
            Dictionary with video information or None on error
        """
        try:
            # Execute JS and get result
            if self._page is None:
                return ""
            playlist_data = await self._page.evaluate("RTCCertificate.plstdic")

            if not playlist_data:
                logging.warning("No playlist data found in RTCCertificate.plstdic")
                return ""

            logging.info(f"Raw playlist data: {playlist_data[:500]}...")

            return playlist_data

        except Exception as e:
            logging.exception(f"Error getting playlist dict: {e}")
            return ""

    def _normalize_translation_key(self, translation: str) -> str:
        """Normalize length of translation."""
        prefix = VideoTranslationCallback.__prefix__
        encoded_len = len(f"{prefix}:{translation}".encode())
        if encoded_len > 64:
            _translation = re.sub(
                r'[^A-Za-z0-9\s!@#$%^&*()_+\-=\[\]{};:\'",.<>/?\\|`~]',
                "",
                translation,
            ).strip()
            if not _translation:
                _translation = str(hash(translation))
        else:
            _translation = translation
        return _translation

    def _normalize_episode_name(self, episode_name: str) -> tuple[str, str | None]:
        """Delete 3d part from episode_name split by space if exists."""
        part1, part2, *part3 = episode_name.split(" ")
        return f"{part1} {part2}", part3[0] if part3 else None

    def _parse_playlist_string(self, playlist_str: str) -> dict[str, Any]:
        """
        Parses the playlist string into a structured dictionary.

        Args:
            playlist_str: JSON string or raw playlist data

        Returns:
            Dictionary with structure:
            {
                'seasons': [...],  # Original seasons with file as dict
                'translations': {translation_key: translation_name, ...},
                'qualities': ['360p', '720p', ...]
            }
        """
        try:
            playlist = json.loads(playlist_str)
        except json.decoder.JSONDecodeError:
            playlist = [
                {
                    "title": "1 сезон",
                    "folder": [{"title": "1 серия", "file": playlist_str}],
                },
            ]

        # Handle single episode without folder structure
        if "folder" not in playlist[0]:
            _playlist: list[dict[str, Any]] = [{"title": "1 сезон", "folder": []}]
            for episode in playlist:
                _playlist[0]["folder"].append(episode)
            playlist = _playlist

        result: dict[str, Any] = {
            "seasons": [],
            "perevod_from_html": (
                self._perevod_from_html
                if "," not in (self._perevod_from_html, "")
                else ""
            ),
            "translations": {},
            "qualities": set(),
        }

        for season in playlist:
            season_title = season.get("title", "")
            folder = season.get("folder", [])

            processed_folder = []

            for episode in folder:
                episode_id = episode.get("id")
                episode_title, perevod = self._normalize_episode_name(
                    episode.get("title", ""),
                )
                file_data = episode.get("file", "")

                # Parse file field into structured format
                parsed_file = self._parse_file_field(file_data, perevod or "")

                # Collect qualities and translations
                for quality in parsed_file:
                    result["qualities"].add(quality)

                result["translations"] = self._translation_names

                processed_episode = {
                    "id": episode_id,
                    "title": episode_title,
                    "file": parsed_file,
                    "perevod": perevod,
                }

                processed_folder.append(processed_episode)

            result["seasons"].append(
                {"title": season_title, "folder": processed_folder},
            )

        qualities_list = sorted(
            result["qualities"],
            key=lambda x: int(x.replace("p", "")),
        )
        result["qualities"] = qualities_list

        return result

    def _parse_file_field(self, file_data: str, perevod: str) -> dict[str, Any]:
        """
        Parses the file field string into a structured dictionary.

        Format: [360p]{trans1}url1 or url2;{trans2}url3 or url4,[720p]...

        Args:
            file_data: Raw file string
            perevod: Translation from html

        Returns:
            Dictionary: {quality: {translation_key: [urls]}}
        """
        result: dict[str, Any] = {}

        # Step 1: Split by ';' to separate different audio tracks
        # Format: [360p]content or [720p]content or ...
        semicolon_parts = file_data.replace(",", ";").split(";")

        for _semicolon_part in semicolon_parts:
            if not _semicolon_part.strip():
                continue

            if "p]" in _semicolon_part:
                quality, semicolon_part = _semicolon_part.split("]")
                quality = quality.lstrip("[")
            else:
                pass  # todo del

            if quality not in result:
                result[quality] = {}

            # Step 3: Split by '{' to check for translation
            if "{" in semicolon_part:
                # Has translation in curly braces
                translation_name, urls_part = semicolon_part.split("}")
                translation_name = translation_name.lstrip("{").strip()

                translation_key = self._normalize_translation_key(translation_name)

                self._translation_names[translation_key] = translation_name

                # Step 4: Split by ' or ' to get URLs
                urls = self._split_urls(urls_part)

                result[quality][translation_key] = urls
            else:
                # No translation in braces, treat as URLs
                urls = self._split_urls(semicolon_part)
                if "," in (self._perevod_from_html or ""):
                    translation_key = "FromEpisode" if perevod else "Unknown"
                else:
                    translation_key = self._normalize_translation_key(
                        self._perevod_from_html or "",
                    )
                result[quality][translation_key] = urls

        return result

    def _split_urls(self, urls_part: str) -> list[str]:
        """
        Splits a string by ' or ' to extract URLs.

        Args:
            urls_part: String containing URLs separated by ' or '

        Returns:
            List of valid URLs
        """
        if not urls_part:
            return []

        urls = []
        # Step 4: Split by ' or '
        for url_part in urls_part.split(" or "):
            url = url_part.strip()
            if url and url.startswith("http"):
                urls.append(url)

        return urls

    def _get_language_code(self, language_name: str) -> str | None:
        """Converts Russian language name to ISO 639-1 code."""  # todo del
        # Mapping of Russian language names to ISO 639-1 codes
        russian_to_iso = {
            "русский": "ru",
            "украинский": "uk",
            "английский": "en",
            "немецкий": "de",
            "французский": "fr",
            "испанский": "es",
            "итальянский": "it",
            "китайский": "zh",
            "японский": "ja",
            "польский": "pl",
            "турецкий": "tr",
            "арабский": "ar",
            "хинди": "hi",
        }

        # Normalize input: lowercase and strip whitespace
        normalized_name = language_name.lower().strip()

        # Return the code if found, otherwise None
        return russian_to_iso.get(normalized_name)

    def _create_ytdlp_formats(
        self,
        playlist_dict: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Converts the playlist dictionary to yt-dlp format.

        Args:
            playlist_dict: Dictionary from _get_playlist_dic

        Returns:
            List of formats in yt-dlp style
        """
        # Resolution mapping for common qualities todo del
        resolution_map = {
            "144p": {"width": 256, "height": 144},
            "240p": {"width": 426, "height": 240},
            "360p": {"width": 640, "height": 360},
            "480p": {"width": 854, "height": 480},
            "720p": {"width": 1280, "height": 720},
            "1080p": {"width": 1920, "height": 1080},
            "1440p": {"width": 2560, "height": 1440},
            "2160p": {"width": 3840, "height": 2160},
            "4K": {"width": 3840, "height": 2160},
        }

        formats = []

        for quality in playlist_dict["qualities"]:
            # Get dimensions from resolution map
            dimensions = resolution_map.get(quality, {"width": None, "height": None})
            height = dimensions["height"]
            width = dimensions["width"]

            # If quality not in map, extract height from string
            if height is None:
                height = int(quality.replace("p", ""))
                width = int(height * 16 / 9) if height else None
            format_info = {
                "format_id": f"{quality}",
                "url": None,
                "ext": None,
                "height": None,
                "width": None,
                "protocol": "https",
                "video_ext": None,
                "audio_ext": None,
                "vcodec": None,
                "acodec": None,
                "resolution": f"{width}x{height}" if width and height else quality,
                "language": None,
                "format_note": quality,
                "quality": height,
            }

            formats.append(format_info)

        return formats

    def _video_info_from_dto(
        self,
        videotheatre_dto: VideoTheatreDTO,
    ) -> dict[str, Any]:
        playlist_dict = videotheatre_dto.info_dict
        if not isinstance(playlist_dict, dict):
            return {}
        formats = self._create_ytdlp_formats(playlist_dict)
        # Build result in yt-dlp style
        source_id = self._resolution.url.split("/")[-1]
        video_info = {
            "id": source_id,
            "title": videotheatre_dto.title,
            "original_url": self._resolution.url,
            "url": self._resolution.url,
            "seasons": json.loads(videotheatre_dto.model_dump_json()),
            "formats": formats,
            "ext": "mp4",
            "thumbnail": videotheatre_dto.thumbnail_url,
            "duration": None,
            "width": None,
            "height": None,
        }

        logging.info(
            f"Successfully extracted video info with {len(formats)} formats",
        )
        return video_info

    async def _parse_kinovod(self) -> str:
        # Start CDP session
        await self.start_cdp()

        if not self._page:
            raise Exception("Failed to create page")

        # Setup request interception for playerjs.js
        await self._page.route(
            re.compile(r".*/playerjs.js.*"),
            self._change_playerjs,
        )

        # Load film page
        await self._load_film(self._resolution.url)

        # Wait for video element to load
        await self._check_load()

        # Get thumbainl
        self._thumbnail_url = await self._get_thumb()

        # Translation info
        self._perevod_from_html = await self._parse_perevod()

        # Get playlist data
        return await self._get_playlist_dict()

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Gets video information from kinovod.pro.

        Main logic:
        1. Loads page via Playwright
        2. Intercepts and modifies playerjs.js
        3. Extracts playlist data
        4. Converts to yt-dlp format
        """
        try:
            source_id = self._resolution.url.split("/")[-1]
            cachemodel = await self.get_dto_from_cache(
                source_id,
                settings.dto_expire_timeout,
            )
            if cachemodel:
                videotheatre_dto = cachemodel.meta_data_dto
                if not isinstance(videotheatre_dto, VideoTheatreDTO):
                    return None
                self._perevod_from_html = videotheatre_dto.perevod_from_html
                if not isinstance(videotheatre_dto.info_dict, str):
                    return None
                playlist_dict = self._parse_playlist_string(videotheatre_dto.info_dict)
                videotheatre_dto.info_dict = playlist_dict

                # Build result in yt-dlp style
                return self._video_info_from_dto(videotheatre_dto)

            playlist_str = await self._parse_kinovod()
            if not playlist_str:
                logging.warning("No playlist dict found, may be captcha")
                return None

            videotheatre_dto = VideoTheatreDTO.from_kinovod(
                playlist_str,
                self._resolution.url,
                title=self._title,
                thumbnail_url=self._thumbnail_url,
                proxy=self._proxy,
                perevod_from_html=self._perevod_from_html,
            )
            await self.create_or_update_cache_entry(videotheatre_dto)
            # Parse the received string into a dictionary
            playlist_dict = self._parse_playlist_string(playlist_str)
            videotheatre_dto.info_dict = playlist_dict

            return self._video_info_from_dto(videotheatre_dto)

        except Exception as e:
            logging.exception(f"Error in get_video_info: {e}")
            return None
        finally:
            await self._cleanup_resources()

    async def download_video(self) -> None:  # noqa: PLR0912, PLR0915, C901
        """
        Main entry point for Kinovod video download.

        Workflow:
        1. Check fsm context
        2. Get direct video URL
        3. Download video via yt-dlp
        4. Send to Telegram
        """
        url = self._resolution.url

        if not url:
            await self._send_error_message()
            return

        try:
            # 1. Check fsm context data
            video_info = await self._telegram_bot_controller.get_fsm_data(
                user_id=self._telegram_id,
                chat_id=self._telegram_id,
            )
            if not video_info:
                return
            quality_label = video_info.get("quality_label", "")
            season_label = video_info.get("season_label", "")
            translation_label = video_info.get("translation_label", "")
            episode_label = video_info.get("episode_label", "")
            info_dict_fsm = json.loads(video_info["info_dict"])
            info_dict = info_dict_fsm["seasons"]["info_dict"]

            video_data = VideoTheatreDTO.from_kinovod(info_dict_fsm["seasons"], url)
            self._proxy = video_data.proxy
            self._yt_dlp.params.update({"proxy": self._proxy})
            info_dict = video_data.info_dict
            if not isinstance(info_dict, dict):
                return
            seasons = info_dict["seasons"]
            if season_label:
                season = next(item for item in seasons if item["title"] == season_label)
            else:
                season = seasons[0]
            if episode_label:
                episode = next(
                    item for item in season["folder"] if item["title"] == episode_label
                )
            else:
                episode = season["folder"][0]
            if quality_label in episode["file"]:
                if translation_label:
                    video_urls = episode["file"][quality_label][translation_label]
                else:
                    ((_, video_urls),) = episode["file"][quality_label].items()
            else:
                info_dict["qualities"].reverse()
                for quality in info_dict["qualities"]:
                    if quality in episode["file"]:
                        video_urls = episode["file"][quality][translation_label]
                        break

            season_num = season_label.split(" ")[0]
            episode_num = episode_label.split(" ")[0]
            self._source_id = info_dict_fsm["id"] + f"_{season_num}_{episode_num}"

            # 2: Check cache first
            if await self.send_video_from_cache(self._source_id, quality_label):
                return

            logging.info(
                "Cache miss for source_id=%s and quality=%s. Starting download.",
                self._source_id,
                quality_label,
            )
            self._process_percent(16)

            video_dto = await self._download_video_by_urls(video_urls)

            self._process_percent(86)

            if not video_dto:
                await self._send_error_message()
                return

            video_dto.source_id = self._source_id
            video_dto.url = self._resolution.url
            video_dto.title = video_info["video_dto"]["title"]
            video_dto.quality = quality_label
            if video_data.perevod_from_html and "," not in video_data.perevod_from_html:
                video_dto.translation = video_data.perevod_from_html
            if self._resolution.metadata["type"] != "film" and season_label:
                video_dto.season = season_label
                video_dto.episode = episode_label
                if (
                    not video_dto.translation
                    and len(seasons[0]["folder"][0]["file"][quality_label]) > 1
                    and isinstance(info_dict, dict)
                ):
                    video_dto.translation = info_dict["translations"][translation_label]

            #  3: Send to Telegram

            await self._send_video(video_dto)

            #  4: Cleanup
            self.cleanup_files([video_dto])

        except Exception as e:
            logging.exception("Error in Kinovod download process: %s", e)
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
        finally:
            await self._cleanup_resources()

    async def _cleanup_resources(self) -> None:
        """Clean up browser resources."""
        # Stop slippers proxy if it was started
        if self._proxy_local:
            self._proxy_local.stop()
            logging.info("Stopped slippers proxy")
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
