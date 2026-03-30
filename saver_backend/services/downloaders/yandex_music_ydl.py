import asyncio
import logging
from typing import Any, ClassVar, List

from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.schema import AudioDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController


class YandexMusicController(YtDlpController):
    """
    Controller for downloading audio from Yandex Music.

    Handles:
    - music.yandex.ru/track/<id> (single track)
    - music.yandex.ru/album/<id> (full album)
    """

    SOURCE: ClassVar[SourceEnum] = SourceEnum.YANDEX_MUSIC_YDL
    COOKIES: ClassVar[bool] = True
    SUPPORTS_STREAMING: ClassVar[bool] = True
    DIRECT_URL_DOWNLOAD: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with Yandex Music specific yt-dlp parameters."""
        super().__init__(*args, **kwargs)
        yandex_music_params = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                },
                {
                    "key": "FFmpegMetadata",
                    "add_metadata": True,
                },
                {
                    "key": "EmbedThumbnail",
                },
            ],
            "writethumbnail": True,
            "quiet": True,
            "noprogress": True,
            "overwrites": True,
        }
        self._yt_dlp.params.update(yandex_music_params)

    async def _process_track(
        self,
        track_id: str,
        track_url: str,
    ) -> AudioDTO | None:
        """
        Process a single track (Cache Check -> Download).

        :param track_id: The ID of the track.
        :param track_url: The full URL of the track.
        :return: AudioDTO if successful, None otherwise.
        """
        # 1. Check Cache
        cached_item = await self._cache_dao.get_by_filters(
            source=self.SOURCE,
            source_id=track_id,
            quality="best",
        )

        if cached_item and isinstance(cached_item.meta_data_dto, AudioDTO):
            logging.info("Cache hit for track %s. Using file_id.", track_id)
            cached_dto = cached_item.meta_data_dto.model_copy()
            cached_dto.direct_download_url = cached_item.file_id
            cached_dto.path = None
            return cached_dto

        # 2. Download Audio
        logging.info("Downloading track: %s", track_url)

        try:
            info_dict = await asyncio.to_thread(
                self._yt_dlp.extract_info,
                url=track_url,
                download=True,
            )

            if not info_dict:
                logging.error("No info returned for track %s", track_url)
                return None

            # Determine the output file path
            track_file = info_dict.get("requested_downloads", [{}])[0].get("filepath")
            if not track_file and info_dict.get("filepath"):
                track_file = info_dict.get("filepath")

            if not track_file:
                # Fallback: build path from ID
                track_file = str(self._download_directory / f"{track_id}.mp3")

            return AudioDTO.from_yandexmusic(
                audio_data=info_dict,
                resolution_url=self._resolution.url,
            )

        except DownloadError as e:
            error_msg = str(e)
            if "HTTP Error 403" in error_msg or "HTTP Error 429" in error_msg:
                logging.warning("Rate limited or access denied: %s", error_msg)
                return None
            logging.error("Failed to download track %s: %s", track_url, e)
            return None
        except Exception:
            logging.exception("Unexpected error downloading track %s", track_url)
            return None

    async def _handle_single_track(self, track_id: str) -> None:
        """
        Handle downloading of a single track.

        :param track_id: The ID of the track.
        """
        track_url = self._resolution.url
        logging.info("Processing single track: %s", track_id)

        self._process_percent(16)

        audio_dto = await self._process_track(
            track_id=track_id,
            track_url=track_url,
        )

        if not audio_dto:
            await self._send_error_message()
            return

        self._process_percent(86)

        # Send the audio
        await self._send_audio(audio_dto=audio_dto)

        # Cleanup
        self.cleanup_files([audio_dto])

    async def _send_audio_group(self, audio_dtos: List[AudioDTO]) -> None:
        # Send audio group todo mark for del
        for i, audio_dto in enumerate(audio_dtos):
            should_del = (i == len(audio_dtos) - 1) and (self._message_id is not None)
            msg_id_to_del = self._message_id if should_del else None

            await self._telegram_bot_controller.send_finish_downloading_audio(
                audio=audio_dto,
                telegram_id=self._telegram_id,
                message_id=msg_id_to_del,
            )

    async def _handle_album(self, album_id: str) -> None:
        """
        Handle downloading of a full album.

        :param album_id: The ID of the album.
        """
        album_url = self._resolution.url
        logging.info("Processing album: %s", album_id)

        self._process_percent(9)

        # First, get album info to extract track IDs
        try:
            info_dict = await asyncio.to_thread(
                self._yt_dlp.extract_info,
                url=album_url,
                download=False,
            )
        except DownloadError as e:
            logging.exception("Failed to fetch album info: %s", e)
            await self._send_error_message()
            return

        # Extract tracks from the album
        entries = info_dict.get("entries", [])
        if not entries:
            logging.warning("No tracks found in album %s", album_id)
            await self._send_error_message()
            return

        total_tracks = len(entries)
        logging.info("Found %d tracks in album %s", total_tracks, album_id)

        self._process_percent(15)

        # Process each track
        audio_dtos: List[AudioDTO] = []
        for idx, entry in enumerate(entries):
            track_id = entry.get("id") or entry.get("display_id")
            if not track_id:
                logging.warning("Could not determine track ID for entry: %s", entry)
                continue

            track_url = (
                entry.get("webpage_url") or f"https://music.yandex.ru/track/{track_id}"
            )

            audio_dto = await self._process_track(
                track_id=track_id,
                track_url=track_url,
            )

            if audio_dto:
                audio_dtos.append(audio_dto)

            # Update progress based on tracks processed
            percent = 20 + int((idx + 1) / total_tracks * 51)
            self._process_percent(percent)

        if not audio_dtos:
            logging.warning("No tracks successfully downloaded from album %s", album_id)
            await self._send_error_message()
            return

        self._process_percent(73)

        await self._telegram_bot_controller.send_finish_downloading_doc_group(
            files=audio_dtos,
            telegram_id=self._telegram_id,
            message_id=self._message_id,
        )

        # Cleanup
        self.cleanup_files(audio_dtos)

    async def download_video(self) -> None:
        """Main entry point: Routes to single track or album logic based on metadata."""
        code = self._resolution.metadata.get("code")
        content_type = self._resolution.metadata.get("type")

        if not code:
            logging.error("No code found in resolution metadata")
            await self._send_error_message()
            return

        self._process_percent(11)

        if content_type == "album":
            await self._handle_album(code)
        else:
            await self._handle_single_track(code)
