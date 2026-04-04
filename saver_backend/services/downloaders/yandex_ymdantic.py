import asyncio
import logging
import secrets
from pathlib import Path
from typing import Any, ClassVar

import httpx
from ymdantic import YMClient
from ymdantic.exceptions import YMError

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import AudioDTO
from saver_backend.settings import settings


class YmdanticController(BaseSourceController):
    """
    Controller for downloading music from Yandex.Music via ymdantic.

    Handles:
    - Tracks: Direct download links
    - Albums: All tracks from an album
    """

    SOURCE: ClassVar[SourceEnum] = SourceEnum.YMDANTIC
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.ALL

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        if not settings.ym_token:
            logging.error("YM_TOKEN is not set in .env")

        proxy_url = self._proxy
        self._client = YMClient(
            token=secrets.choice(settings.ym_token),
            proxy=proxy_url or None,
        )
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)

        # HTTP client for downloading files
        self._http_client = httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            proxy=proxy_url,
        )

    async def close(self) -> None:
        """Close the YMClient and HTTP client resources."""
        await self._http_client.aclose()
        if hasattr(self._client, "close"):
            await self._client.close()

    async def _get_track(
        self,
        track_id: int,
        album_id: str | None = None,
    ) -> AudioDTO | None:
        """
        Get track download information and create AudioDTO.

        :param track_id: ID of the track to download.
        :return: AudioDTO with download URL or None on failure.
        """
        try:
            # Get direct download info
            download_infos = await self._client.get_track_download_info_direct(
                track_id=track_id,
            )

            if not download_infos:
                logging.warning("No download info found for track %s", track_id)
                return None

            # Sort by bitrate_in_kbps
            download_infos.sort(key=lambda x: x.bitrate_in_kbps, reverse=True)

            download_info = download_infos[0]
            direct_url = str(download_info.direct_url)

            # Get track metadata
            track = await self._client.get_track(track_id=track_id)

            return AudioDTO.from_yandmatic(
                audio_url=direct_url,
                track=track,
                resolution_url=self._resolution.url,
                album_id=album_id,
            )

        except YMError as ye:
            logging.error(f"Unauthorized access to Yandex.Music API. Check token. {ye}")
            return None
        except Exception as e:
            logging.error("Failed to get track %s: %s", track_id, e)
            return None

    async def _get_album_tracks(self, album_id: int) -> list[AudioDTO]:
        """
        Get all tracks from an album.

        :param album_id: ID of the album.
        :return: List of AudioDTOs for all tracks in the album.
        """
        try:
            album_with_tracks = await self._client.get_album_with_tracks(
                album_id=album_id,
            )

            track_ids: list[int] = []
            for volume in album_with_tracks.volumes:
                for track in volume:
                    track_ids.append(track.id)

            # Fetch all tracks concurrently
            album_id_str = str(album_id)
            tasks = [self._get_track(track_id, album_id_str) for track_id in track_ids]
            results = await asyncio.gather(*tasks)

            dtos = [dto for dto in results if dto is not None]

            logging.info(
                "Successfully fetched %d/%d tracks from album %s",
                len(dtos),
                len(track_ids),
                album_id,
            )
            return dtos

        except Exception as e:
            logging.exception("Failed to get album %s: %s", album_id, e)
            return []

    async def _download_audio_file(
        self,
        audio_dto: AudioDTO,
        filename: str,
    ) -> Path | None:
        """
        Download audio file from direct URL.

        :param audio_dto: AudioDTO containing download URL.
        :param filename: Target filename.
        :return: Local file path or None on failure.
        """

        if not audio_dto.media_url:
            return None

        file_path = self._download_directory / filename

        try:
            async with self._http_client.stream("GET", audio_dto.media_url) as response:
                response.raise_for_status()
                with file_path.open("wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)

            return file_path

        except Exception as e:
            logging.error("Failed to download audio file %s: %s", filename, e)
            if file_path.exists():
                file_path.unlink()
            return None

    async def _send_audio_track(
        self,
        audio_dto: AudioDTO,
        download: bool = False,
    ) -> None:
        """
        Send a single audio track to Telegram, downloading it first.

        :param audio_dto: AudioDTO to send.
        """
        if not download:
            await self._send_audio(audio_dto)
            return
        # Generate safe filename
        safe_title = "".join(
            c for c in (audio_dto.title or "track") if c.isalnum() or c in "._- "
        )[:50]
        filename = f"{audio_dto.source_id}_{safe_title}.mp3"

        # Download the file
        local_path = await self._download_audio_file(audio_dto, filename)

        if not local_path:
            await self._send_error_message()
            return

        # Update DTO with local path
        audio_dto.path = local_path
        audio_dto.direct_download_url = None

        # Send to Telegram
        await self._send_audio(audio_dto)

        # Cleanup
        self.cleanup_files([audio_dto])

    async def _send_audio_album(
        self,
        audio_dtos: list[AudioDTO],
        download: bool = False,
    ) -> None:
        """
        Send an album (multiple audio tracks) to Telegram.

        :param audio_dtos: List of AudioDTOs to send.
        """
        if not audio_dtos:
            await self._send_error_message()
            return

        self._process_percent(47)

        # Download all tracks
        for i, audio_dto in enumerate(audio_dtos):
            if audio_dto.source_id is None:
                continue
            self._process_percent(min(50 + int(i / len(audio_dtos) * 21), 71))
            cached_dto = await self._check_dto_in_cache(
                audio_dto.source_id,
                audio_dto.quality or "best",
            )
            if cached_dto:
                audio_dtos[i] = cached_dto
                continue
            if download:
                safe_title = "".join(
                    c
                    for c in (audio_dto.title or "track")
                    if c.isalnum() or c in "._- "
                )[:50]
                filename = f"{audio_dto.source_id}_{safe_title}.mp3"

                local_path = await self._download_audio_file(audio_dto, filename)
                if local_path:
                    audio_dto.path = local_path
                    audio_dto.direct_download_url = None

        self._process_percent(73)

        # Send as audio group
        await self._send_audio_group(audio_dtos)

        # Cleanup
        self.cleanup_files(audio_dtos)

    async def _check_dto_in_cache(
        self,
        source_id: str,
        quality: str = "best",
    ) -> AudioDTO | None:
        """
        Check if audio exists in cache by source_id and return cached version if found.

        :param source_id: Source ID of the audio (track ID).
        :param quality: Quality of the audio.
        :return: Cached AudioDTO with file_id or None if not found.
        """
        cached_item = await self._cache_dao.get_by_filters(
            source=self.SOURCE,
            source_id=source_id,
            quality=quality,
        )

        if not cached_item or not isinstance(cached_item.meta_data_dto, AudioDTO):
            return None

        cached_dto = cached_item.meta_data_dto.model_copy()
        cached_dto.direct_download_url = cached_item.file_id
        cached_dto.path = None

        logging.info(
            "Cache hit for source_id=%s, quality=%s.",
            source_id,
            quality,
        )

        return cached_dto

    async def download_video(self) -> None:
        """
        Main entry point for downloading Yandex.Music content.

        Routes to track or album logic based on metadata type.
        """
        code = self._resolution.metadata.get("code")
        content_type = self._resolution.metadata.get("type", "track")

        if not code:
            logging.error("No 'code' found in resolution metadata.")
            await self._send_error_message()
            return

        self._process_percent(16)

        if content_type == "album":
            # Album download
            audio_dtos = await self._get_album_tracks(int(code))

            if audio_dtos:
                await self._send_audio_album(audio_dtos)
            else:
                await self._send_error_message()
        else:
            # Single track download
            # Try cache for single track
            if await self.send_audio_from_cache(source_id=str(code), quality="best"):
                return
            audio_dto = await self._get_track(int(code))
            if audio_dto:
                await self._send_audio_track(audio_dto)
            else:
                await self._send_error_message()

    async def send_audio_from_cache(self, source_id: str, quality: str) -> bool:
        """
        Send audio from cache if available.

        :param source_id: Source ID of the audio (track ID).
        :param quality: Quality of the audio.
        :return: True if audio was sent from cache, False otherwise.
        """
        cached_item = await self._cache_dao.get_by_filters(
            source=self.SOURCE,
            source_id=source_id,
            quality=quality,
        )

        if not cached_item or not isinstance(cached_item.meta_data_dto, AudioDTO):
            return False

        if not cached_item:
            return False

        await self._create_history_entry(cached_item)

        cached_dto = cached_item.meta_data_dto.model_copy()
        cached_dto.direct_download_url = cached_item.file_id
        cached_dto.path = None

        await self._send_audio(cached_dto)
        return True
