import asyncio
import logging
from typing import Any, ClassVar, Optional
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    ProxySettings,
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
            "quiet": True,
            "noprogress": True,
            "format": "best[ext=mp4]/best",
        }
        self._yt_dlp.params.update(kinovod_params)

        # Store browser and page for cleanup
        self._browser: Optional[Browser] = None
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._source_id: Optional[str] = None
        self._title: Optional[str] = None

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

    async def _download_video_by_url(self, video_url: str) -> Optional[VideoDTO]:
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
                url=video_url,
                download=True,
            )

            file_id = info_dict.get("id", self._source_id)
            predicted_path = (
                self._download_directory / f"{file_id}.{info_dict.get('ext', 'mp4')}"
            )

            video_dto = VideoDTO.from_yt_dlp(
                info=info_dict,
                file_path=predicted_path,
                quality="best",
            )

            video_dto.thumbnail = self._get_thumbnail(file_id)

            return video_dto

        except DownloadError as e:
            logging.error("Failed to download video %s: %s", video_url, e)
            return None
        except Exception as e:
            logging.exception("Unexpected error downloading video %s: %s", video_url, e)
            return None

    async def start_cdp(self) -> None:
        """
        Start cdp session.

        :return:
        """
        # Initialize Playwright with proxy support
        playwright = await async_playwright().start()
        self._playwright = playwright

        # Get Chrome CDP URL from settings
        chrome_cdp_url = settings.chrome_cdp_url

        logging.info("Connecting to Chrome CDP: %s", chrome_cdp_url)

        # Connect to existing Chrome container
        browser = await playwright.chromium.connect_over_cdp(chrome_cdp_url)
        self._browser = browser

        # Create context with proxy if configured
        proxy_settings = None
        if self._proxy:
            parsed_proxy = urlparse(self._proxy)
            server = (
                f"{parsed_proxy.scheme}://{parsed_proxy.hostname}:{parsed_proxy.port}"
            )

            if parsed_proxy.username and parsed_proxy.password:
                proxy_settings = ProxySettings(
                    server=server,
                    username=parsed_proxy.username,
                    password=parsed_proxy.password,
                )
            else:
                proxy_settings = ProxySettings(server=server)
            logging.info("Using proxy: %s", server)

        # Create new context with proxy (this overrides browser proxy settings)
        self._context = await browser.new_context(
            proxy=proxy_settings if proxy_settings else None,
            ignore_https_errors=True,
        )

        self._page = await self._context.new_page()

    async def download_video(self) -> None:
        """
        Main entry point for Kinovod video download.

        Workflow:
        1. Load film page
        2. Check for video or alert
        3. Extract direct video URL
        4. Download video via yt-dlp
        5. Send to Telegram
        """
        url = self._resolution.url

        if not url:
            await self._send_error_message()
            return

        try:

            self._source_id = url.split("/")[-1].split(".")[0]

            # Check cache first
            if await self.send_video_from_cache(self._source_id, "best"):
                return

            logging.info(
                "Cache miss for source_id=%s and quality=%s. Starting download.",
                self._source_id,
                "best",
            )
            self._process_percent(16)

            await self.start_cdp()

            if not self._page:
                raise Exception("Failed to create page")

            await self._load_film(url, self._page)
            self._process_percent(33)

            # Step 2: Check for video or alert
            video_src = await self._check_load(self._page)
            self._process_percent(66)

            if not video_src:
                await self.delete_processing_message()
                await self._telegram_bot_controller.send_content_not_found_error(
                    telegram_id=self._telegram_id,
                )
                return

            # Step 3: Download video
            video_dto = await self._download_video_by_url(video_src)
            self._process_percent(86)

            if not video_dto:
                await self._send_error_message()
                return

            video_dto.source_id = self._source_id
            video_dto.url = self._resolution.url
            video_dto.title = self._title

            # Step 4: Send to Telegram
            await self._send_video(video_dto)

            # Step 5: Cleanup
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
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
