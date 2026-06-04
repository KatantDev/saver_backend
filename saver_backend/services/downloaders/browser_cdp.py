import asyncio
import json
import logging
import secrets
import socket
from pathlib import Path
from typing import Any, ClassVar, Optional

import slippers
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

from saver_backend.settings import settings


class BrowserStart:
    """A browser automation controller that manages a Playwright CDP session."""

    PAGE_LOAD_TIMEOUT: ClassVar[int] = 30000
    ELEMENT_CHECK_INTERVAL: ClassVar[int] = 1000
    MAX_WAIT_TIME: ClassVar[int] = 60000

    def __init__(self, proxy: str, cookie_path: str = "") -> None:
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._proxy = proxy
        self._browser: Optional[Browser] = None
        self._cookies: Optional[list[dict[str, Any]]] = None
        self._cookie_file = cookie_path

    def _load_cookies(self) -> list[dict[str, Any]] | None:
        if (
            not self._cookie_file
            or not self._cookie_file.strip()
            or not Path(self._cookie_file).exists()
            or Path(self._cookie_file).stat().st_size < 10
        ):
            return None
        try:
            with Path(self._cookie_file).open("r", encoding="utf-8") as f:
                cookies = json.load(f)
            if isinstance(cookies, list):
                self._cookies = cookies
                logging.info(
                    "Loaded %s cookies from %s", len(cookies), self._cookie_file
                )
                return cookies
        except Exception as e:
            logging.exception("Cookie errror: %s.", e)
        return None

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
        upstream_proxy_url = self._proxy

        logging.info("Starting slippers proxy on :%d -> %s", port, upstream_proxy_url)
        self._proxy_local = slippers.Proxy(
            upstream_proxy_url,
            host=settings.taskiq_worker_host,
            port=port,
        )
        self._proxy_local.start()
        await asyncio.sleep(3)

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
        # Add cookies to context
        if self._cookies:
            await self._context.add_cookies(self._cookies)  # type: ignore
            logging.info(f"Added {len(self._cookies)} cookies to context")

        self._page = await self._context.new_page()

    async def load_url(self, url: str) -> None:
        """Load the page and wait for initial load."""

        if self._page is None:
            return

        # Navigate to the page
        await self._page.goto(
            url=url,
            wait_until="domcontentloaded",
            timeout=self.PAGE_LOAD_TIMEOUT,
        )

    async def fetch_js_url_encoded(
        self, url: str, data: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """
        Executes a POST request with URL-encoded data via JS fetch.

        Args:
            url: Request URL
            data: Dictionary with data to send in urlencoded format

        Returns:
            Dictionary with request result or None on error
        """
        try:
            if self._page is None:
                logging.error("Page not initialized for fetch_js_url_encoded")
                return None

            logging.info(f"fetch url: {url}")

            # Convert Python dict to JavaScript object
            data_json = json.dumps(data)

            # JavaScript code for fetch request with URLSearchParams
            js_code = f"""
            (async () => {{
                try {{
                    const data = {data_json};

                    // URLSearchParams can be created directly from an object
                    const params = new URLSearchParams(data);

                    const response = await fetch("{url}", {{
                        method: "POST",
                        headers: {{
                            "Content-Type":
                            "application/x-www-form-urlencoded; charset=UTF-8",
                            "Accept": "*/*",
                            "X-Requested-With": "XMLHttpRequest",
                        }},
                        body: params
                    }});

                    if (!response.ok) {{
                        throw new Error(`HTTP error! status: ${{response.status}}`);
                    }}

                    return await response.json();
                }} catch (error) {{
                    console.error("Fetch error:", error);
                    return {{ error: error.message }};
                }}
            }})()
            """
            result = await self._page.evaluate(js_code)

            if result and isinstance(result, dict) and "error" not in result:
                logging.info(f"Successfully fetched data from URL: {url}")
                return result
            if result and isinstance(result, dict) and "error" in result:
                logging.error(f"Error in fetch_js_url_encoded: {result['error']}")
                return None
            logging.warning(f"No data returned from URL: {url}")
            return None

        except PlaywrightTimeoutError:
            logging.error(f"Timeout during fetch_js_url_encoded for URL: {url}")
            return None
        except Exception as e:
            logging.exception(f"Unexpected error in fetch_js_url_encoded: {e}")
            return None

    async def cleanup_resources(self) -> None:
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
