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
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController
from saver_backend.settings import settings


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

    async def close(self) -> None:
        """Close browser and page resources."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        await super().close()

    async def _load_film(self, url: str, page: Page) -> None:
        """
        Load the film page and wait for initial load.

        Args:
            url: Kinovod film URL
            page: Playwright page instance
        """
        logging.info("Loading film page: %s", url)

        # Navigate to the page
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self.PAGE_LOAD_TIMEOUT,
        )

    async def _check_load(self, page: Page) -> Optional[str]:
        """
        Check for alert or video element.

        Returns:
            Direct video URL if video found, None if alert found or timeout

        Raises:
            Exception: If alert message is found
        """
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = (asyncio.get_event_loop().time() - start_time) * 1000
            if elapsed > self.MAX_WAIT_TIME:
                logging.error(
                    "Timeout waiting for video or alert after %d ms",
                    self.MAX_WAIT_TIME,
                )
                return None

            # Check for alert
            try:
                alert_element = await page.wait_for_selector(
                    self.ALERT_SELECTOR,
                    timeout=self.ELEMENT_CHECK_INTERVAL,
                )
                if alert_element:
                    alert_text = await alert_element.text_content()
                    logging.error("Alert found on page: %s", alert_text)
                    raise Exception(f"Site error: {alert_text}")
            except PlaywrightTimeoutError:
                pass  # No alert found, continue

            # Check for video element with src
            try:
                video_element = await page.wait_for_selector(
                    self.VIDEO_SELECTOR,
                    timeout=self.ELEMENT_CHECK_INTERVAL,
                )
                if video_element:
                    video_src = await video_element.get_attribute("src")
                    if video_src:
                        h1_element = await page.wait_for_selector(
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

    async def _get_thumb(self, page: Page) -> Optional[str]:
        """
        Extracts thumbnail URL from the page.

        Looks for element .poster > img and gets its src attribute.

        Args:
            page: Playwright page instance

        Returns:
            Thumbnail URL or None if not found
        """
        try:
            # Wait for poster image element
            poster_img = await page.query_selector(".poster > img")

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

    async def _download_video_by_url(
        self,
        video_url: str,
        quality: str = "best",
    ) -> Optional[VideoDTO]:
        """
        Download video using yt-dlp.

        Args:
            video_url: Direct video URL from the video tag

        Returns:
            VideoDTO if successful, None otherwise
        """

        # Download via yt-dlp
        logging.info("Downloading video: %s", video_url)
        try:
            info_dict = await asyncio.to_thread(
                self._yt_dlp.extract_info,
                url=video_url.strip(),
                download=True,
            )

            # downloaded video path
            predicted_path = self._download_directory / f"{info_dict['id']}.mp4"

            return VideoDTO.from_yt_dlp(
                info=info_dict,
                file_path=predicted_path,
                quality=self._selected_format_id or "best",
            )

        except DownloadError as e:
            logging.error("Failed to download video %s: %s", video_url, e)
            return None
        except Exception as e:
            logging.exception("Unexpected error downloading video %s: %s", video_url, e)
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

    async def _get_playlist_dic(self, page: Page) -> Optional[dict[str, Any]]:
        """
        Executes JavaScript to get the playlist dictionary.

        Returns:
            Dictionary with video information or None on error
        """
        try:
            # Execute JS and get result
            playlist_data = await page.evaluate("RTCCertificate.plstdic")

            if not playlist_data:
                logging.warning("No playlist data found in RTCCertificate.plstdic")
                return None

            logging.info(f"Raw playlist data: {playlist_data[:500]}...")

            # Parse the received string into a dictionary
            return self._parse_playlist_string(playlist_data)

        except Exception as e:
            logging.exception(f"Error getting playlist dict: {e}")
            return None

    def _parse_playlist_string(self, playlist_str: str) -> dict[str, Any]:
        """
        Parses the playlist string into a dictionary.

        Format: [360p]{lang1}url1 or url2 or url3;{lang2}url4 or url5 or url6,[720p]...

        Returns:
            Dictionary format: {
                '360p': [
                    {'lang': 'Dubbed (Russian)', 'url': 'url1'},
                    {'lang': 'Dubbed (Ukrainian)', 'url': 'url2'}
                ],
                '720p': [...]
            }
        """
        result: dict[str, Any] = {}

        # Split by comma (quality separator)
        quality_parts = playlist_str.split(",")

        for quality_part in quality_parts:
            # Split by semicolon (language separator)
            language_parts = quality_part.split(";")

            # Process first element (contains quality info)
            first_language_part = language_parts[0]
            # Extract quality from [quality]{...}
            if "]{" in first_language_part:
                quality = first_language_part.split("]{")[0].lstrip("[")
                # Extract language and URLs
                lang_and_urls = first_language_part.split("]{")[1]
                language = lang_and_urls.split("}")[0]
                urls_part = lang_and_urls.split("}")[1]
            else:
                quality = first_language_part.split("]")[0].lstrip("[")
                urls_part = first_language_part.split("]")[1]
                language = None
            first_url = urls_part.split(" or ")[0]

            # Save to result
            if quality not in result:
                result[quality] = []
            result[quality].append({"lang": language, "url": first_url})

            # Process remaining languages (if any)
            for i in range(1, len(language_parts)):
                language = language_parts[i].split("}")[0]
                urls_part = language_parts[i].split("}")[1]
                first_url = urls_part.split(" or ")[0]

                result[quality].append({"lang": language, "url": first_url})

        # Sort qualities by height (ascending)
        sorted_result = dict(
            sorted(result.items(), key=lambda x: int(x[0].replace("p", ""))),
        )

        logging.info(f"Parsed playlist: {sorted_result.keys()}")
        return sorted_result

    def _get_language_code(self, language_name: str) -> str | None:
        """Converts Russian language name to ISO 639-1 code."""
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
        # Resolution mapping for common qualities
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

        for quality, variants in playlist_dict.items():
            # Get dimensions from resolution map
            dimensions = resolution_map.get(quality, {"width": None, "height": None})
            height = dimensions["height"]
            width = dimensions["width"]

            # If quality not in map, extract height from string
            if height is None:
                height = int(quality.replace("p", ""))
                width = int(height * 16 / 9) if height else None

            for variant in variants:
                lang = variant.get("lang", None)
                if lang:
                    lang = lang.split("(")[1].strip().rstrip(")")
                    lang = self._get_language_code(lang)
                url = variant.get("url")

                if not url:
                    continue

                # Create format_id based on quality and language
                lang_slug = "_" + lang.lower() if lang else ""
                format_id = f"{quality}{lang_slug}"
                # for compatibility with yt-dlp
                ext = url.split(".")[-1].split("/")[0]
                format_info = {
                    "format_id": f"{ext}/{format_id}",
                    "url": url,
                    "ext": ext,
                    "height": height,
                    "width": width,
                    "protocol": "https",
                    "video_ext": ext,
                    "audio_ext": "mp4",
                    "vcodec": None,
                    "acodec": None,
                    "resolution": f"{width}x{height}" if width and height else quality,
                    "language": lang,
                    "format_note": lang,
                    "quality": height,
                }

                formats.append(format_info)

        return formats

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
            await self._load_film(url, self._page)

            # Wait for video element to load
            video_src = await self._check_load(self._page)

            # Get playlist data
            playlist_dict = await self._get_playlist_dic(self._page)

            # Get thumbainl
            thumb = await self._get_thumb(self._page)

            if not playlist_dict:
                logging.warning("No playlist dict found, using direct video URL")
                # If playlist not available, use direct URL
                return {
                    "id": self._resolution.url.split("/")[-1],
                    "title": self._title,
                    "url": video_src,
                    "formats": [
                        {
                            "format_id": "direct",
                            "url": video_src,
                            "ext": "mp4",
                            "height": None,
                            "resolution": "unknown",
                        },
                    ],
                }

            # Create formats for yt-dlp
            formats = self._create_ytdlp_formats(playlist_dict)

            first_format = formats[0]

            source_id = self._resolution.url.split("/")[-1]
            # Build result in yt-dlp style
            ext = first_format.get("ext", "mp4")
            video_info = {
                "id": source_id,
                "title": self._title,
                "original_url": url,
                "url": video_src,
                "formats": formats,
                "ext": ext,
                "thumbnail": thumb,
                "duration": None,
                "width": first_format["width"],
                "height": first_format["height"],
            }

            logging.info(
                f"Successfully extracted video info with {len(formats)} formats",
            )
            return video_info

        except Exception as e:
            logging.exception(f"Error in get_video_info: {e}")
            return None
        finally:
            await self._cleanup_resources()

    async def download_video(self) -> None:
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
            video_info = json.loads(video_info["info_dict"])
            self._source_id = video_info["id"]
            quality = (self._selected_format_id or "").split("/")[-1]
            # 2: Check cache first
            if await self.send_video_from_cache(self._source_id, quality):
                return

            logging.info(
                "Cache miss for source_id=%s and quality=%s. Starting download.",
                self._source_id,
                quality,
            )
            self._process_percent(16)
            target_format: dict[str, Any] | None = next(
                (
                    item
                    for item in video_info["formats"]
                    if item.get("format_id") == self._selected_format_id
                ),
                None,
            )
            if target_format is None:
                logging.warning("target_format is None")
                return
            video_src = target_format["url"]

            video_dto = await self._download_video_by_url(video_src)

            self._process_percent(86)

            if not video_dto:
                await self._send_error_message()
                return

            video_dto.source_id = self._source_id
            video_dto.url = self._resolution.url
            video_dto.title = video_info["title"]
            video_dto.quality = quality

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
