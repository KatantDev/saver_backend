import asyncio
import logging
import re
import secrets
import socket
from collections import deque
from datetime import datetime, timedelta
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

from saver_backend.entities.enums import ContentTypeEnum, ProxyType, SourceEnum
from saver_backend.services.downloaders.exceptions import (
    Kinovod404Error,
    KinovodAlertError,
    KinovodCaptchaError,
    KinovodMirrorError,
)
from saver_backend.services.downloaders.schema import (
    VideoDTO,
    VideoTheatreDTO,
)
from saver_backend.services.downloaders.schemes.KinovodSchema import KinovodDTO
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
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.RU
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

        # Content type for aiogram filter
        self.contenttype = ContentTypeEnum.FILM_DICT
        # Store browser and page for cleanup
        self._browser: Optional[Browser] = None
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._source_id: Optional[str] = None
        self._title: Optional[str] = None
        self._proxy_local: Optional[slippers.Proxy] = None
        self._proxies_rotate: deque[str] = deque(self._proxies)
        self._perevod_from_html: str = ""
        self._thumbnail_url: Optional[str] = None
        self._mirror_url: Optional[str] = None
        self._used_mirrors: list[str] = []

    async def close(self) -> None:
        """Close browser and page resources."""
        await self._cleanup_resources()
        await super().close()

    async def _load_film(self) -> None:
        """Load the film page and wait for initial load."""

        url = self._mirror_url if self._mirror_url else self._resolution.url

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

    async def _check_element_exists(
        self,
        selector: str,
        exception_class: Optional[type[Exception]] = None,
        error_message: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> bool:
        """
        Generic method to check if an element exists on the page.

        Args:
            selector: CSS or XPath selector to look for
            exception_class: Optional exception to raise if element is found
            error_message: Optional error message for the exception
            timeout: Timeout in ms for waiting
             (uses ELEMENT_CHECK_INTERVAL if not specified)

        Returns:
            bool: True if element exists, False otherwise

        Raises:
            exception_class: If element is found and exception_class is provided
        """
        if self._page is None:
            return False

        timeout_ms = timeout or self.ELEMENT_CHECK_INTERVAL

        try:
            element = await self._page.wait_for_selector(
                selector,
                timeout=timeout_ms,
            )

            if element:
                logging.debug("Element found: %s", selector)
                if exception_class:
                    error_msg = (
                        error_message or f"Element found with selector: {selector}"
                    )
                    logging.error(error_msg)
                    raise exception_class(error_msg)
                return True
            return False

        except PlaywrightTimeoutError:
            return False

    async def _check_alert(self) -> None:
        """Check if alert div exists on the page. Raises KinovodAlertError if found."""
        await self._check_element_exists(
            selector=self.ALERT_SELECTOR,
            exception_class=KinovodAlertError,
            error_message="Site error: alert found on page",
        )

    async def _check_captcha(self) -> None:
        """Check if captcha image exists on the page."""
        await self._check_element_exists(
            selector='//img[@id="captcha_image"]',
            exception_class=KinovodCaptchaError,
            error_message="Captcha found on page",
        )

    async def _check_page_not_found(self) -> None:
        """Check if 404 error div exists on the page."""
        await self._check_element_exists(
            selector='//div[@id="error404"]',
            exception_class=Kinovod404Error,
            error_message="[Kinovod] Page not found",
        )

    async def _check_load(self) -> Optional[str]:
        """
        Check for alert or video element or not found element.

        Returns:
            Direct video URL if video found,
            None if alert or 404 element found or timeout

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
                    "Timeout waiting for video or alert after %d ms",
                    self.MAX_WAIT_TIME,
                )
                return None

            try:
                # Check for alert, captcha, and 404 using unified method
                await self._check_alert()
                await self._check_captcha()
                await self._check_page_not_found()
            except (KinovodAlertError, KinovodCaptchaError, Kinovod404Error):
                raise
            except Exception as e:
                # Log unexpected errors but continue checking
                logging.debug(f"Unexpected error during element checks: {e}")

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
            video_urls: Direct video URLs from the video tracks

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

    def _get_mirror(self) -> Optional[str]:
        """Generate mirror url."""
        today = datetime.now()
        for d in range(5, -1, -1):
            mirror_date = (today - timedelta(days=d)).strftime("%d%m%y")
            mirror_host = f"kinovod{mirror_date}.pro"

            if mirror_host not in self._used_mirrors:
                self._used_mirrors.append(mirror_host)
                logging.info(f"[kinovod] Found mirror: {mirror_host}")
                self._mirror_url = self._resolution.url.replace(
                    "/kinovod.pro",
                    f"/{mirror_host}",
                )
                return self._mirror_url

        raise KinovodMirrorError("[kinovod] Could not find mirror")

    async def _prepare_proxy(self, timeout: int = 5) -> bool:
        """
        Search working proxy.

        :param url: URL to test the proxy against.
        :param timeout: Timeout in seconds.
        :return: True if proxy works, False otherwise.
        """
        if not self._proxy:
            return False

        url = self._resolution.url

        while True:
            try:
                async with httpx.AsyncClient(
                    proxy=self._proxy,
                    timeout=timeout,
                ) as client:
                    response = await client.get(url)
                    return response.status_code < 500
            except Exception:
                logging.warning(f"Bad mirror: {url}")
                url = self._get_mirror() or ""
                continue
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
        await self._prepare_proxy()
        upstream_proxy_url = self._proxy

        logging.info("Starting slippers proxy on :%d -> %s", port, upstream_proxy_url)
        self._proxy_local = slippers.Proxy(
            upstream_proxy_url,
            host=settings.taskiq_worker_host,
            port=port,
        )
        self._proxy_local.start()
        await asyncio.sleep(3)

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
            modified_body = re.sub(
                r"function Playerjs\(options\)\s*\{",
                r"function Playerjs(options){RTCCertificate.plstdic = options.file;",
                body,
            )

            await route.fulfill(
                response=response,
                body=modified_body,
            )
        except Exception as e:
            logging.error(f"Error in _change_playerjs: {e}")
            await route.continue_()

    async def _get_playlist_str(self) -> str:
        """
        Executes JavaScript to get the playlist string.

        Returns:
            String with video information or None on error
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

    def _create_ytdlp_formats(
        self,
        playlist_dict: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Converts the playlist dictionary to yt-dlp format.

        Args:
            playlist_dict: Dictionary from Kinovod model dump

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

    # for yt-dlp compatibility
    def _video_info_from_dto(
        self,
        videotheatre_dto: VideoTheatreDTO,
        playlist_str: str,
    ) -> dict[str, Any]:

        kinovod_dto = KinovodDTO(
            url=self._resolution.url,
            playlist_str=playlist_str,
            perevod_from_html=self._perevod_from_html,
        )

        playlist_dict = kinovod_dto.model_dump(exclude_unset=True)
        videotheatre_dto.dto_dict = playlist_dict
        videotheatre_dto.raw_data = ""

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
            "videotheatre_dto": videotheatre_dto.model_dump(exclude_unset=True),
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
            re.compile(r".*/playerjs.*js.*"),
            self._change_playerjs,
        )

        # Load film page
        await self._load_film()

        # Wait for video element to load
        await self._check_load()

        # Get thumbainl
        self._thumbnail_url = await self._get_thumb()

        # Translation info
        self._perevod_from_html = await self._parse_perevod() or ""

        # Get playlist data
        return await self._get_playlist_str()

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
                if not isinstance(videotheatre_dto, VideoTheatreDTO) or not isinstance(
                    videotheatre_dto.raw_data,
                    str,
                ):
                    return None

                self._perevod_from_html = videotheatre_dto.perevod_from_html or ""
                # Build result in yt-dlp style
                return self._video_info_from_dto(
                    videotheatre_dto,
                    videotheatre_dto.raw_data,
                )

            playlist_str = await self._parse_kinovod()
            if not playlist_str:
                logging.warning("No playlist dict found, may be captcha")
                return None

            videotheatre_dto = VideoTheatreDTO.from_raw_data(
                playlist_str,
                self._resolution.url,
                title=self._title or "",
                thumbnail_url=self._thumbnail_url or "",
                proxy=self._proxy or "",
                perevod_from_html=self._perevod_from_html or "",
            )
            await self.create_or_update_cache_entry(videotheatre_dto)
            # Parse the received string into a dictionary

            return self._video_info_from_dto(videotheatre_dto, playlist_str)

        except Kinovod404Error:
            await self.delete_processing_message()
            self._message_id = None
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
        except Exception as e:
            logging.exception(f"[kinovod] Error in get_video_info: {e}")

        return None

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
            fsm_data = await self._telegram_bot_controller.get_fsm_data(
                user_id=self._telegram_id,
                chat_id=self._telegram_id,
            )
            if not fsm_data:
                return

            videotheatre_dto = VideoTheatreDTO.from_fsm_data(
                fsm_data=fsm_data,
                resolution=self._resolution,
            )

            self._proxy = videotheatre_dto.proxy
            self._yt_dlp.params.update({"proxy": self._proxy})

            # 2: Check cache first
            if await self.send_video_from_cache(
                videotheatre_dto.source_id or "",
                videotheatre_dto.quality_real or "",
            ):
                return

            logging.info(
                "Cache miss for source_id=%s and quality=%s. Starting download.",
                videotheatre_dto.source_id,
                videotheatre_dto.quality_real or "",
            )
            self._process_percent(16)

            video_urls = videotheatre_dto.selected_track.urls

            video_dto = await self._download_video_by_urls(video_urls)

            self._process_percent(86)

            if not isinstance(video_dto, VideoDTO):
                await self._send_error_message()
                return

            video_dto = VideoDTO.from_kinovod(
                video_dto=video_dto,
                videotheatre_dto=videotheatre_dto,
                resolution=self._resolution,
            )

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
