import asyncio
import base64
import binascii
import hashlib
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, unquote, urlparse

import ffmpeg
import httpx
from httpx import AsyncClient
from yt_dlp import DownloadError

from saver_backend.entities.enums import (
    FsmKeysEnum,
    InstagramContentTypeEnum,
    ProxyType,
    SourceEnum,
)
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.exceptions import InstaIndownError
from saver_backend.services.downloaders.instagram_instaloader_source import (
    InstagramInstaloaderController,
)
from saver_backend.services.downloaders.instagram_session import (
    YDL_COOKIES_FILE,
    is_login_required_error,
    request_session_check,
)
from saver_backend.services.downloaders.instagram_ydl_source import (
    InstagramYdlController,
    is_content_unavailable,
)
from saver_backend.services.downloaders.schema import (
    PhotoDTO,
    VideoDTO,
)
from saver_backend.settings import settings

# indown.io allows 50 downloads a day per address (21.11 prod: 9 of 12 proxies
# spent by morning). A spent proxy is skipped for a while; the time of the
# daily reset is unknown, so it is tried again after a few hours.
INDOWN_LIMIT_TEXT = "Too many requests"
INDOWN_LIMIT_PAUSE_SECONDS = 3 * 60 * 60
INDOWN_MAX_ATTEMPTS = 6
# When every proxy is marked as spent, a few are still tried: the mark is a
# guess (the reset time is unknown) and 64 of 353 such tries on prod on
# 23.11 went through.
INDOWN_SPENT_ATTEMPTS = 3


def _proxy_key(proxy: str | None) -> str:
    """Short hash of a proxy, so the shared state never keeps its address."""
    return hashlib.sha1((proxy or "direct").encode()).hexdigest()[:12]  # noqa: S324


def story_media_id(url: str) -> int | None:
    """
    Read the Instagram media id of a story photo from its CDN link.

    Photo links carry ``ig_cache_key``: the base64 media id plus a suffix.
    Story videos have no such key.

    :param url: CDN link returned by indown.io.
    :return: Media id, the number in /stories/<user>/<id>/, or None.
    """
    key = parse_qs(urlparse(url).query).get("ig_cache_key", [""])[0]
    if not key:
        return None
    head = key.split(".")[0]
    try:
        raw = base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)).decode()
    except binascii.Error, UnicodeDecodeError, ValueError:
        return None
    return int(raw) if raw.isdigit() else None


CACHE_KEY_RE = re.compile(r"ig_cache_key=([A-Za-z0-9_=-]+)")
# Items of a highlight sent when the link does not say which story was open
HIGHLIGHT_ITEMS_WITHOUT_STORY = 10


def media_id_in(text: str) -> int | None:
    """
    Instagram media id from the last ``ig_cache_key`` in a piece of indown.io HTML.

    Every item of the answer has a picture: the photo itself or the poster of
    a video, behind indown.io's own address. Its ``ig_cache_key`` is the base64
    media id, so a video is matched as exactly as a photo.

    :param text: HTML around one item.
    :return: Media id or None.
    """
    keys = CACHE_KEY_RE.findall(unquote(unquote(text.replace("&amp;", "&"))))
    if not keys:
        return None
    head = keys[-1].split(".")[0]
    try:
        raw = base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)).decode()
    except binascii.Error, UnicodeDecodeError, ValueError:
        return None
    raw = raw.split("_")[0]
    return int(raw) if raw.isdigit() else None


def pick_story_links(links: list[str], story_id: int) -> list[str]:
    """
    Keep only the story a /stories/<user>/<id>/ link points to (task 17).

    indown.io returns every current story of the account, oldest first. A
    photo is matched by its media id. A video has no id in its link, but ids
    grow with time, so the story sits between the photos with a smaller and a
    larger id; when exactly one video is there, that is the story.

    :param links: CDN links of all current stories, in indown.io order.
    :param story_id: Media id from the story link.
    :return: The matching links; all links when the story cannot be told apart.
    """
    ids = [story_media_id(link) for link in links]
    if story_id in ids:
        return [links[ids.index(story_id)]]

    known = [media_id for media_id in ids if media_id is not None]
    newest_first = len(known) > 1 and known[0] > known[-1]
    order = list(range(len(links)))
    if newest_first:
        order.reverse()

    before = [pos for pos, i in enumerate(order) if (ids[i] or story_id) < story_id]
    after = [pos for pos, i in enumerate(order) if (ids[i] or story_id) > story_id]
    start = max(before) + 1 if before else 0
    stop = min(after) if after else len(order)
    candidates = [order[pos] for pos in range(start, stop) if ids[order[pos]] is None]
    if len(candidates) == 1:
        return [links[candidates[0]]]
    return links


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
        "Chrome/151.0.0.0 Safari/537.36"
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)

        self._proxy_type = self.PROXY_TYPE
        self._client: AsyncClient | None = None

    async def _init_httpx(self, proxy: str | None) -> None:
        if self._client:
            await self._client.aclose()
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
            proxy=proxy,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()

    def _normalize_url(self, url: str) -> str:
        """
        Fixes Instagram URLs for indown.io.

        Known issue: indown fails with /reels/ (plural), needs /reel/ (singular).
        """
        if "/reels/" in url:
            return url.replace("/reels/", "/reel/")
        return url

    async def _get_main_page(self, referer: str) -> str | None:
        """Fetch the main page HTML."""
        if self._client is None:
            return None
        try:
            response = await self._client.get(referer)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logging.error(f"Failed to fetch main page from {referer}: {e}")
            return None

    def _get_referer(self, html: str) -> str:
        """Extract referer from HTML."""
        match = re.search(r'name="referer" value="([^"]+)"', html)
        return match.group(1) if match else ""

    def _get_csrf_token(self, html: str) -> str:
        """Extract CSRF token from HTML."""
        match = re.search(r'name="_token" value="([^"]+)"', html)
        return match.group(1) if match else ""

    async def _get_additional_post_param(self, html_main_page: str) -> tuple[str, str]:
        """
        Extract additional POST parameters from asset JS file.

        Returns tuple of (param_name, param_value) or empty strings if not found.
        """
        if self._client is None:
            return "e", "e"

        # Extract asset JS URL
        match = re.search(r'src="([^"]*assets/app-[^"]*\.js)"', html_main_page)
        if not match:
            logging.warning("Could not find asset JS URL in main page")
            return "e", "e"

        asset_js_url = match.group(1)
        if not asset_js_url.startswith("http"):
            asset_js_url = f"{self.BASE_URL}{asset_js_url}"

        try:
            response = await self._client.get(asset_js_url)
            response.raise_for_status()
            js_content = response.text

            pattern = (
                r'e\.name\s*=\s*([`"\'])([^`"\']+)\1\s*,'
                r'\s*e\.value\s*=\s*([`"\'])([^`"\']+)\3'
            )
            match = re.search(pattern, js_content)

            if match:
                # param_name is group 2, param_value is group 4
                return match.group(2), match.group(4)

        except Exception as e:
            logging.error(f"Failed to fetch asset JS: {e}")

        return "e", "e"

    async def _get_form_fields(self, referer: str) -> list[str] | None:
        """Fetch the page to get the valid CSRF token and additional params."""
        if self._client is None:
            return None

        html = await self._get_main_page(referer)
        if not html:
            return None

        token = self._get_csrf_token(html)
        referer_val = self._get_referer(html)

        # Get additional POST parameters from asset JS
        param_name, param_value = await self._get_additional_post_param(html)

        # Return token, referer, and additional params
        return [token, referer_val, param_name, param_value]

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
        if self._is_highlight():
            # one highlight holds many stories: the key names the story too
            code = self._resolution.metadata.get("code")
            return f"hl{code}_{self._resolution.metadata.get('story') or 'all'}"
        path = urlparse(url).path.rstrip("/")
        return path.split("/")[-1]

    async def _download_file(self, url: str, filename: str) -> None:
        """
        Download file from URL to local storage.

        :param url: Direct URL to file.
        :param filename: Target filename.
        """
        file_path = self._download_directory / filename
        if "indown.io/344.jpg" in url:
            raise InstaIndownError(f"[indown] Error 344; {self._proxy}")

        if self._client is None:
            return
        try:
            await self._stream_to_file(self._client, url, file_path)
            return
        except Exception as e:
            # A warning: the file is asked for once more below
            logging.warning(
                "Download of %s through the proxy failed (%s), trying directly",
                filename,
                type(e).__name__,
            )
        # The files are on a CDN that does not care where the request comes
        # from, while a slow or dead proxy lost 128 of them on prod on 23.11
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": self.USER_AGENT},
                timeout=30,
                follow_redirects=True,
            ) as direct_client:
                await self._stream_to_file(direct_client, url, file_path)
        except Exception as e:
            logging.error(f"Failed to download file {url}: {type(e).__name__}: {e}")
            if file_path.exists():
                file_path.unlink()
            raise

    @staticmethod
    async def _stream_to_file(client: AsyncClient, url: str, file_path: Path) -> None:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            # Written in chunks so a big video is not held in memory
            with file_path.open("wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)

    async def _set_proxy_type_from_fsm(self) -> None:
        fsm_data = await self._telegram_bot_controller.get_fsm_data(
            user_id=int(FsmKeysEnum.INSTAGRAM_INDOWN),
            chat_id=int(FsmKeysEnum.INSTAGRAM_INDOWN),
        )
        if fsm_data and fsm_data.get("proxy_type", "") == "ru":
            self._proxy_type = ProxyType.RU
            self._select_proxies(self._proxy_type)

    async def _change_proxy_type(self) -> None:
        if self._proxy_type == ProxyType.RU:
            self._proxy_type = ProxyType.LOCAL
        elif self._proxy_type == ProxyType.LOCAL:
            self._proxy_type = ProxyType.RU

        self._select_proxies(self._proxy_type)

        await self._telegram_bot_controller.set_fsm_data(
            user_id=int(FsmKeysEnum.INSTAGRAM_INDOWN),
            chat_id=int(FsmKeysEnum.INSTAGRAM_INDOWN),
            data={"proxy_type": self.PROXY_TYPE.value},
        )

    async def download_video(self) -> None:
        """
        Main execution flow.

        Posts and reels are first read the way a guest sees them: that needs
        no indown.io quota (50 a day per address, 600 for the whole pool against
        about 1000 Instagram requests a day) and returns photos and carousels
        as well as videos. indown.io comes second and keeps its quota for what
        only it can get: stories and posts hidden from guests. The service
        account, when its session exists, is the last resort.
        """
        await self._set_proxy_type_from_fsm()

        original_url = self._resolution.url
        source_id = self._extract_source_id(original_url)

        # 1. Check Cache
        if await self.send_video_from_cache(source_id=source_id, quality="best"):
            return

        self._process_percent(10)
        is_story = self._is_story()
        is_highlight = self._is_highlight()
        guest_outcome = "skipped"
        # Stories and highlights are not shown to a guest at all
        if not is_story and not is_highlight:
            guest_outcome = await self._try_ydl(source_id, use_session=False)
            if guest_outcome == "done":
                return

        indown_outcome = await self._ask_indown(original_url, source_id)
        if indown_outcome == "done":
            return

        if is_story:
            await self._fallback_to_instaloader()
            return
        if YDL_COOKIES_FILE.exists() and not is_highlight:
            account_outcome = await self._try_ydl(source_id, use_session=True)
            if account_outcome == "done":
                return
        logging.error(
            "Instagram content not received: %s; user_id=%s; guest: %s, indown.io: %s",
            original_url,
            self._telegram_id,
            guest_outcome,
            indown_outcome,
        )
        # A guest request is refused for posts that exist too (age limit, rate
        # limit), so "private or deleted" needs indown.io's word: either it
        # says so, or it was asked and has nothing while the guest request
        # says the post is unavailable. When indown.io could not be asked
        # (the quota is spent), the user gets the general error.
        not_found = indown_outcome == "not_found" or (
            indown_outcome == "empty" and guest_outcome == "unavailable"
        )
        if is_highlight and indown_outcome == "empty":
            # indown.io names a missing highlight in words of its own
            not_found = True
        if not_found and not self._inline_query_id:
            await self._send_not_found_message()
        else:
            await self._send_error_message()

    def _is_story(self) -> bool:
        content_type = self._resolution.metadata.get("type")
        return content_type == InstagramContentTypeEnum.STORIES

    def _is_highlight(self) -> bool:
        content_type = self._resolution.metadata.get("type")
        return content_type == InstagramContentTypeEnum.HIGHLIGHTS

    async def _ask_indown(self, original_url: str, source_id: str) -> str:
        """
        Ask indown.io through the proxies that still have their daily quota.

        :param original_url: Link the user sent.
        :param source_id: Cache key of the content.
        :return: "done" when the content was sent, "not_found" when indown.io
            says it is missing or private, "empty" when it answered without
            any media, "failed" when it could not be asked or its files could
            not be downloaded.
        """
        answered_empty = False
        for proxy in await self._indown_routes():
            outcome = await self._try_indown(proxy, original_url, source_id)
            if outcome == "empty":
                answered_empty = True
            elif outcome != "next":
                return outcome
        logging.warning("No media found in indown.io response.")
        return "empty" if answered_empty else "failed"

    async def _try_ydl(self, source_id: str, use_session: bool) -> str:
        """
        Read the post through yt-dlp and send what it has.

        :param source_id: Cache key of the content.
        :param use_session: Read as the service account instead of a guest.
        :return: "done" when the content was sent to the user, "unavailable"
            when Instagram says the post is closed or gone, "failed" otherwise.
        """
        url = self._resolution.url
        controller = InstagramYdlController(
            resolution=self._resolution,
            telegram_bot_controller=self._telegram_bot_controller,
            telegram_id=self._telegram_id,
            user_dao=self._user_dao,
            history_dao=self._history_dao,
            cache_dao=self._cache_dao,
            message_id=self._message_id,
            inline_query_id=self._inline_query_id,
            use_session=use_session,
        )
        who = "the service account" if use_session else "a guest"
        try:
            media_items = await controller.fetch_media(url)
        except DownloadError as error:
            logging.warning("Instagram refused %s as %s: %s", url, who, error.msg)
            if use_session and is_login_required_error(error):
                await request_session_check()
            return "unavailable" if is_content_unavailable(error) else "failed"
        except Exception as error:
            logging.warning(
                "Reading %s as %s failed: %s: %s",
                url,
                who,
                type(error).__name__,
                error,
            )
            return "failed"
        finally:
            await controller.close()
        if not media_items:
            logging.warning("Instagram returned no media for %s as %s", url, who)
            return "failed"

        logging.info("Instagram %s read as %s: %s items", url, who, len(media_items))
        if len(media_items) == 1 and isinstance(media_items[0], VideoDTO):
            # Telegram takes a short video by its link, nothing is downloaded
            video = media_items[0]
            video.source_id = source_id
            await self._send_video(video)
            return "done"
        for item in media_items:
            item.source_id = source_id
        try:
            await self._init_httpx(self._proxy)
            await self._send_media_result(media_items)
        except InstaIndownError:
            self.cleanup_files(media_items)
            return "failed"
        return "done"

    async def _try_indown(  # noqa: PLR0911 - one return per indown.io outcome
        self,
        proxy: str | None,
        original_url: str,
        source_id: str,
    ) -> str:
        """
        One indown.io attempt through one proxy.

        :param proxy: Proxy to use, None for the server's own address.
        :param original_url: Link the user sent.
        :param source_id: Cache key of the content.
        :return: "done" when the content was sent, "not_found" when indown.io
            says the content is missing, "failed" when its files could not be
            downloaded, "empty" when it answered without media (the next proxy
            is still tried), "next" when it could not be asked through this
            proxy.
        """
        referer_url = f"{self.BASE_URL}/reels/"
        await self._init_httpx(proxy)
        if self._client is None:
            return "next"
        form_fields = await self._get_form_fields(referer_url)
        if not form_fields:
            logging.error("Could not fetch CSRF token from indown.io")
            return "next"
        referer = form_fields[1]
        self._client.headers.update({"Referer": referer})
        payload = {
            "link": self._normalize_url(original_url),
            "referer": referer,
            "locale": "en",
            "_token": form_fields[0],
            (form_fields[2] if len(form_fields) > 2 else "e"): (
                form_fields[3] if len(form_fields) > 3 else "e"
            ),
        }
        try:
            response = await self._client.post(
                f"{self.BASE_URL}/download", data=payload
            )
            response.raise_for_status()
            html = response.text
        except Exception as e:
            logging.warning("indown.io request failed: %s", type(e).__name__)
            return "next"

        self._process_percent(40)
        if INDOWN_LIMIT_TEXT in html:
            # 50 downloads a day per address: try the next proxy
            await self._mark_exhausted(proxy)
            return "next"
        if "Not Found" in html or "Private Video" in html:
            return "not_found"

        media_items = self._extract_media_dtos(html, original_url, source_id)
        if not media_items:
            logging.warning("indown.io failed to parse media")
            return "empty"
        try:
            await self._send_media_result(media_items)
        except InstaIndownError:
            self.cleanup_files(media_items)
            return "failed"
        return "done"

    async def _indown_routes(self) -> list[str | None]:
        """
        Proxies to try for indown.io, those over the daily limit left out.

        indown.io allows 50 downloads a day per address. With two random tries
        the bot kept hitting spent proxies (9 of 12 on prod on 21.11 morning)
        and fell back to yt-dlp, which cannot get photo posts without a login.

        :return: Proxies to try in order; None means the server's own address.
        """
        exhausted = await self._exhausted_proxies()
        pool = [*settings.proxies, *settings.proxies_ru]
        random.shuffle(pool)
        fresh = [p for p in pool if _proxy_key(p) not in exhausted]
        routes: list[str | None] = []
        routes.extend(fresh[:INDOWN_MAX_ATTEMPTS])
        if not routes:
            logging.warning("All indown.io proxies are over the daily limit")
            routes = [*pool[:INDOWN_SPENT_ATTEMPTS]] if pool else [None]
        return routes

    async def _exhausted_proxies(self) -> set[str]:
        data = await self._telegram_bot_controller.get_fsm_data(
            user_id=int(FsmKeysEnum.INDOWN_LIMITS),
            chat_id=int(FsmKeysEnum.INDOWN_LIMITS),
        )
        now = time.time()
        return {
            key
            for key, until in (data or {}).items()
            if isinstance(until, (int, float)) and until > now
        }

    async def _mark_exhausted(self, proxy: str | None) -> None:
        logging.warning("indown.io daily limit reached on a proxy, trying another")
        await self._telegram_bot_controller.update_fsm_data(
            user_id=int(FsmKeysEnum.INDOWN_LIMITS),
            chat_id=int(FsmKeysEnum.INDOWN_LIMITS),
            data={_proxy_key(proxy): time.time() + INDOWN_LIMIT_PAUSE_SECONDS},
        )

    async def _fallback_to_instaloader(self) -> None:
        logging.warning(
            "indown.io failed, falling back to instaloader: %s; user_id=%s",
            self._resolution.url,
            self._telegram_id,
        )
        controller = InstagramInstaloaderController(
            resolution=self._resolution,
            telegram_bot_controller=self._telegram_bot_controller,
            telegram_id=self._telegram_id,
            user_dao=self._user_dao,
            history_dao=self._history_dao,
            cache_dao=self._cache_dao,
            message_id=self._message_id,
            inline_query_id=self._inline_query_id,
        )
        try:
            await controller.download_video()
        finally:
            await controller.close()

    async def _send_not_found_message(self) -> None:
        if self._message_id:
            await self._telegram_bot_controller.delete_message(
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )
        await self._telegram_bot_controller.send_content_not_found_error(
            self._telegram_id,
        )

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

    def _extract_items(self, html: str) -> list[tuple[str, int | None]]:
        """
        Links of the answer with the media id of each item.

        :param html: indown.io answer.
        :return: (link, media id) per item, in indown.io order.
        """
        items: list[tuple[str, int | None]] = []
        position = 0
        for group in re.finditer(
            r'<div class="btn-group-vertical">([\s\S]*?)</div>',
            html,
        ):
            # the picture of an item stands before its buttons
            before = html[position : group.start()]
            position = group.end()
            for href in re.findall(r'href="([^"]+)"', group.group(1)):
                clean = self._extract_clean_url(href)
                if clean and clean not in [link for link, _id in items]:
                    items.append((clean, media_id_in(before) or media_id_in(href)))
                    break
        return items

    def _pick_by_media_id(
        self,
        items: list[tuple[str, int | None]],
    ) -> list[str] | None:
        """
        Keep the one story the link points to, when its id is in the answer.

        :param items: (link, media id) per item.
        :return: The picked links, None when the links should be picked the
            old way.
        """
        wanted = self._story_id()
        links = [link for link, _id in items]
        if wanted is not None:
            for link, media_id in items:
                if media_id == wanted:
                    return [link]
        if self._is_highlight():
            if wanted is not None:
                logging.warning(
                    "Story %s is not among %s items of the highlight",
                    wanted,
                    len(items),
                )
            return links[:HIGHLIGHT_ITEMS_WITHOUT_STORY]
        return None

    def _extract_media_dtos(
        self,
        html: str,
        original_url: str,
        source_id: str,
    ) -> list[PhotoDTO | VideoDTO]:
        """Main parsing logic converting HTML to DTOs."""
        items = self._extract_items(html)
        clean_links = [link for link, _id in items]
        story_id = self._story_id()
        picked_by_id = self._pick_by_media_id(items) if len(items) > 1 else None
        if picked_by_id is not None:
            logging.info(
                "Story %s: %s of %s indown.io items kept by media id",
                story_id,
                len(picked_by_id),
                len(clean_links),
            )
            clean_links = picked_by_id
        elif story_id is not None and len(clean_links) > 1:
            picked = pick_story_links(clean_links, story_id)
            logging.info(
                "Story %s: %s of %s indown.io items kept",
                story_id,
                len(picked),
                len(clean_links),
            )
            clean_links = picked

        media_items = []
        for link in clean_links:
            dto = self._create_dto(link, original_url, source_id)
            media_items.append(dto)

        return media_items

    def _story_id(self) -> int | None:
        """Media id of the story a story or highlight link points to."""
        if self._is_highlight():
            story = str(self._resolution.metadata.get("story") or "")
            return int(story) if story.isdigit() else None
        if self._resolution.metadata.get("type") != InstagramContentTypeEnum.STORIES:
            return None
        code = str(self._resolution.metadata.get("code") or "")
        return int(code) if code.isdigit() else None

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
            except InstaIndownError as e:
                logging.error(
                    f"[InstaIndownError]:"
                    f" {self._resolution.url};"
                    f" user_id={self._telegram_id}; {e}"
                )
                raise e
            except Exception as e:
                logging.error(
                    f"Failed to download/process item {index}:"
                    f" {self._resolution.url};"
                    f" user_id={self._telegram_id}; {e}"
                )

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
