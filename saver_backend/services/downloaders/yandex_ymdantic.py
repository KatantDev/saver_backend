import asyncio
import json
import logging
from pathlib import Path
from typing import Any, ClassVar

import ffmpeg
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
            token=settings.ym_token,
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

    async def _get_track_in_flac(
        self,
        track_id: int,
        album_id: str | None = None,
    ) -> AudioDTO | None:
        """
        Get track download information in flac if available  and create AudioDTO.

        :param track_id: ID of the track to download.
        :return: AudioDTO with download URL or None on failure.
        """
        try:
            # Get direct download info
            download_infos = await self._client.get_track_file_info(
                track_id=track_id,
            )

            if not download_infos:
                logging.warning("No download info found for track %s", track_id)
                return None

            if "-" in download_infos.codec:
                direct_url = str(download_infos.url)

                # Get track metadata
                track = await self._client.get_track(track_id=track_id)

                return AudioDTO.from_yandmatic(
                    audio_url=direct_url,
                    track=track,
                    resolution_url=self._resolution.url,
                    album_id=album_id,
                    quality=download_infos.codec.split("-")[0],
                    codec=download_infos.codec,
                )
            return None

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
            if audio_dto.flac:
                flac_file_path = file_path.with_suffix(".flac")
                ffmpeg.input(str(file_path)).output(
                    str(flac_file_path), acodec="flac"
                ).run()
                file_path = flac_file_path
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
        codec: str = "mp3",
        history: bool = True,
    ) -> None:
        """
        Send a single audio track to Telegram, downloading it first.

        :param audio_dto: AudioDTO to send.
        """
        if not download:
            await self._send_audio(audio_dto, history=history)
            return
        # Generate safe filename
        safe_title = "".join(
            c for c in (audio_dto.title or "track") if c.isalnum() or c in "._- "
        )[:50]
        if "-" in codec:
            ext = "mp4"
            if "flac" in codec:
                audio_dto.flac = True
        else:
            ext = "mp3"
        filename = f"{audio_dto.source_id}_{safe_title}.{ext}"

        # Download the file
        local_path = await self._download_audio_file(audio_dto, filename)

        if not local_path:
            await self._send_error_message()
            return

        # Update DTO with local path
        audio_dto.path = local_path
        audio_dto.media_url = None
        audio_dto.direct_download_url = None

        # Send to Telegram
        await self._send_audio(audio_dto, history=history)

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
                audio_dto.quality or "mp3",
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
                ext = "mp4" if audio_dto.flac else "mp3"
                filename = f"{audio_dto.source_id}_{safe_title}.{ext}"

                if audio_dto.flac:
                    local_path = await self._download_audio_file(audio_dto, filename)
                    if local_path:
                        audio_dto.path = local_path
                        audio_dto.media_url = None
                        audio_dto.direct_download_url = None

        self._process_percent(73)

        # Send as audio group
        await self._send_audio_group(audio_dtos)

        # Cleanup
        self.cleanup_files(audio_dtos)

    async def _check_dto_in_cache(
        self,
        source_id: str,
        quality: str = "mp3",
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

    async def _get_audio_info_from_fsm(self) -> dict[str, Any] | None:
        """
        Retrieve FLAC download information from FSM storage.

        Extracts the 'flac_info' dictionary from user's FSM context data.
        This dictionary contains information about available FLAC version
        of the requested track.

        The returned dictionary structure (if exists):
        {
            'dto': str,  # JSON-serialized AudioDTO for FLAC version
            'download': bool  # Whether user requested FLAC download
        }

        :return: Dictionary with FLAC info from FSM, or None if not found.
        """
        fsm_data = (
            await self._telegram_bot_controller.get_fsm_data(
                user_id=self._telegram_id,
                chat_id=self._telegram_id,
            )
            or {}
        )
        return fsm_data.get("flac_info")

    async def _download_flac(self) -> bool | None:
        """
        Handle FLAC version download workflow for a track.

        This method manages the FLAC availability check and download process:
        1. Checks FSM for existing FLAC info
        2. If FLAC not requested yet, fetches FLAC availability and stores in FSM
        3. If FLAC requested, downloads and sends FLAC version
        4. Cleans up FSM data after completion

        :return:
            - True: FLAC is available
            - False: FLAC is not available for this track
            - None: FLAC download already completed (via button)
        """
        code = self._resolution.metadata.get("code", "0")

        flac_info = await self._get_audio_info_from_fsm()
        if flac_info is None or not flac_info.get("download"):
            flac_dto = await self._get_track_in_flac(track_id=int(code))
            if isinstance(flac_dto, AudioDTO):
                await self._telegram_bot_controller.set_fsm_data(
                    user_id=self._telegram_id,
                    chat_id=self._telegram_id,
                    data={
                        "flac_info": {
                            "dto": flac_dto.model_dump_json(),
                            "download": False,
                        },
                        "resolution": self._resolution.model_dump(mode="json"),
                    },
                )
                return True
            return False
        try:
            flac_dto = AudioDTO.model_validate(json.loads(flac_info.get("dto", "{}")))
            if await self.send_audio_from_cache(
                source_id=str(code),
                quality=flac_dto.quality or "mp3",
            ):
                return None
            if flac_dto:
                await self._send_audio_track(
                    audio_dto=flac_dto,
                    download=flac_dto.flac,
                    codec=flac_dto.codec,
                )
            else:
                await self._send_error_message()
        finally:
            await self._telegram_bot_controller.clear_fsm_data(
                user_id=self._telegram_id,
                chat_id=self._telegram_id,
            )
        return None

    async def _has_any_flac_in_album(self, audio_dtos: list[AudioDTO]) -> bool:
        """
        Check if at least one track in the album has FLAC version available.

        :param audio_dtos: list[AudioDTO].
        :return: True if at least one FLAC track exists, False otherwise.
        """
        try:
            if await self._get_hq_dtos(audio_dtos, check=True):
                return True

        except Exception as e:
            album_id = self._resolution.metadata.get("code")
            logging.exception(
                "Failed to check FLAC availability for album %s: %s", album_id, e
            )
        return False

    async def _store_album_info_to_fsm(self, audio_dtos: list[AudioDTO]) -> None:
        """
        Store album AudioDTO list information in FSM for later FLAC download.

        :param audio_dtos: List of AudioDTO objects to store in FSM.
        """
        serialized_dtos = [dto.model_dump_json() for dto in audio_dtos]

        await self._telegram_bot_controller.set_fsm_data(
            user_id=self._telegram_id,
            chat_id=self._telegram_id,
            data={
                "flac_info": {"dto": serialized_dtos, "download": False},
                "resolution": self._resolution.model_dump(mode="json"),
            },
        )

    async def _get_hq_dtos(
        self,
        audio_dtos: list[AudioDTO],
        check: bool = False,
    ) -> list[AudioDTO]:
        """
        Filter list of AudioDTO objects and return only those with HQ version available.

        :param audio_dtos: List of AudioDTO objects to check for HQ availability.
        :return: List of AudioDTO objects that have HQ version available.
        """
        if not audio_dtos:
            return []

        flac_dtos = []

        for audio_dto in audio_dtos:
            if audio_dto.source_id is None:
                continue

            track_id = int(audio_dto.source_id)
            # Get direct download info
            download_infos = await self._client.get_track_file_info(
                track_id=track_id,
            )

            if not download_infos:
                logging.warning("No download info found for track %s", track_id)
                continue

            if "-" in download_infos.codec:
                if check:
                    flac_dtos.append(audio_dto)
                    break
                direct_url = str(download_infos.url)
            else:
                continue

            audio_dto.media_url = direct_url
            quality = download_infos.codec.split("-")[0]
            audio_dto.track = (audio_dto.track or "").replace(
                " [MP3]", f" [{quality.upper()}]"
            )
            audio_dto.quality = quality
            if quality == "flac":
                audio_dto.flac = True

            flac_dtos.append(audio_dto)

        return flac_dtos

    async def _download_album_in_flac(self) -> bool:
        """
        Handle FLAC version download workflow for an album.

        :return: True if FLAC download completed or dto expired,
         False if no FLAC available (button not clicked)
        """
        audio_info = await self._get_audio_info_from_fsm()

        if audio_info is None or not audio_info.get("download"):
            return False

        try:
            serialized_dtos = audio_info.get("dto", [])
            if not serialized_dtos:
                await self._send_error_message()
                return True

            audio_dtos = []
            for dto_json in serialized_dtos:
                audio_dto = AudioDTO.model_validate(json.loads(dto_json))
                audio_dtos.append(audio_dto)

            flac_dtos = await self._get_hq_dtos(audio_dtos)
            await self._send_audio_album(flac_dtos, download=True)
            return True
        except Exception as e:
            logging.exception("Failed to download album in FLAC: %s", e)
            await self._send_error_message()
            return False
        finally:
            await self._telegram_bot_controller.clear_fsm_data(
                user_id=self._telegram_id,
                chat_id=self._telegram_id,
            )

    async def _send_flac_button(self) -> None:
        """
        Send inline keyboard button for FLAC (lossless) download option.

        Retrieves FLAC AudioDTO from FSM, creates and sends an inline button
        that allows user to download lossless FLAC version of the track.
        Stores the (quality selection) message ID in FSM for later reference.

        The button callback is handled by YmdanticFlacCallback handler.
        """
        flac_info = await self._get_audio_info_from_fsm() or {}
        fsm_dto = flac_info.get("dto", "{}")
        if isinstance(fsm_dto, list):
            fsm_dto = fsm_dto[-1]
            flac_dto = AudioDTO.model_validate(json.loads(fsm_dto))
            if not await self.send_audio_from_cache(
                source_id=str(flac_dto.source_id),
                quality="mp3",
                history=False,
            ):
                await self._send_audio_track(flac_dto, history=False)

        flac_dto = AudioDTO.model_validate(json.loads(fsm_dto))

        await self._telegram_bot_controller.send_hq_button(
            telegram_id=self._telegram_id,
            message_id=self._telegram_bot_controller.last_message_id or 0,
            audio_dto=flac_dto,
        )
        await self._telegram_bot_controller.update_fsm_data(
            user_id=self._telegram_id,
            chat_id=self._telegram_id,
            data={
                "quality_selection_message_id": self._telegram_bot_controller.last_message_id,  # noqa E501
            },
        )

    async def download_video(self) -> None:  # noqa: PLR0912, C901
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
            if await self._download_album_in_flac():
                return
            audio_dtos = await self._get_album_tracks(int(code))

            if audio_dtos:
                if await self._has_any_flac_in_album(audio_dtos):
                    await self._send_audio_album(audio_dtos[:-1])
                    await self._store_album_info_to_fsm(audio_dtos)
                    await self._send_flac_button()
                else:
                    await self._send_audio_album(audio_dtos)
            else:
                await self._send_error_message()
        else:
            is_flac = await self._download_flac()
            if is_flac is None:
                return
            # Single track download
            # Try cache for single track
            if await self.send_audio_from_cache(source_id=str(code), quality="mp3"):
                if is_flac:
                    await self._send_flac_button()
                return
            audio_dto = await self._get_track(int(code))
            if audio_dto:
                await self._send_audio_track(audio_dto)
                if is_flac:
                    await self._send_flac_button()
            else:
                await self._send_error_message()

    async def send_audio_from_cache(
        self, source_id: str, quality: str, history: bool = True
    ) -> bool:
        """
        Send audio from cache if available.

        :param source_id: Source ID of the audio (track ID).
        :param quality: Quality of the audio.
        :param history: flag to save history or not
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

        if history:
            await self._create_history_entry(cached_item)

        cached_dto = cached_item.meta_data_dto.model_copy()
        if "/track" in self._resolution.url:
            cached_dto.url = self._resolution.url
        cached_dto.direct_download_url = cached_item.file_id
        cached_dto.path = None

        await self._send_audio(cached_dto)
        return True
