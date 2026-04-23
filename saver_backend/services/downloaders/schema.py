import json
import logging
import math
import re
import uuid
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

import ffmpeg
from instaloader import Post, PostSidecarNode, StoryItem
from pydantic import BaseModel, Field
from ymdantic.models import TrackType

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.consts import MAX_FILE_SIZE_BYTES
from saver_backend.services.language_resolver import LanguageResolver

if TYPE_CHECKING:
    from aiogram.types import Audio as TgAudio
    from aiogram.types import Video as TgVideo


class BaseContentDTO(BaseModel):
    """Base DTO for any cachable content."""

    source_id: str | None = None
    url: str | None = None
    title: str | None = None
    quality: str | None = "best"


class FormatDTO(BaseModel):
    """Data Transfer Object for a specific video format."""

    format_id: str
    resolution: str
    fps: float = 30.0
    language: str | None = None
    filesize: int | None = None  # bytes

    @property
    def label(self) -> str:
        """
        Generate a user-friendly label for this format's quality (e.g., '1080p').

        This label should NOT include language, as it's used for grouping.

        :return: A string label like '1080p'.
        """
        try:
            parts = self.resolution.split("x")
            if len(parts) == 2:
                height = int(parts[1])
                return f"{height}p"
        except (ValueError, IndexError):
            pass
        return self.resolution

    @property
    def height(self) -> int:
        """
        Extract the video height from the resolution string safely.

        :return: The integer value of the height, or 0 if it cannot be parsed.
        """
        try:
            # Extracts '1080' from '1920x1080'
            return int(self.resolution.split("x")[1])
        except (ValueError, IndexError):
            # Return 0 for non-standard resolutions like "audio only"
            return 0

    @property
    def language_button_text(self) -> str:
        """
        Generate a user-friendly button text for language selection.

        :return: A string for the button, e.g., "English".
        """
        if self.language is None:
            return "Default"
        return LanguageResolver(language=self.language).display_name

    @property
    def formatted_filesize(self) -> str | None:
        """
        Return a human-readable filesize string (e.g., '15.7 MB').

        :return: Human-readable string or None if filesize is not set.
        """
        if self.filesize is None or self.filesize == 0:
            return None
        size_bytes = self.filesize
        if size_bytes == 0:
            return "0 B"
        size_name = ("B", "KB", "MB", "GB", "TB")
        i = math.floor(math.log(size_bytes, 1024))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"

    @classmethod
    def from_yt_dlp(
        cls,
        format_info: dict[str, Any],
        duration: float | None,
    ) -> Optional["FormatDTO"]:
        """
        Create a FormatDTO instance from a yt-dlp format info dictionary.

        :param format_info: The dictionary from yt_dlp.extract_info for a single format.
        :param duration: The total duration of the video in seconds.
        :return: A FormatDTO instance or None if essential data is missing.
        """
        format_id = format_info.get("format_id")
        resolution = format_info.get("resolution")
        if not resolution or len(resolution.split("x")) != 2:
            return None
        if not format_id:
            return None

        filesize = format_info.get("filesize") or format_info.get("filesize_approx")
        if not filesize and duration:
            vbr = format_info.get("vbr") or 0
            abr = format_info.get("abr") or 0
            tbr = format_info.get("tbr") or 0
            total_bitrate_kbps = (vbr + abr) or tbr

            if total_bitrate_kbps > 0:
                total_bitrate_bps = total_bitrate_kbps * 1000 / 8
                filesize = int(total_bitrate_bps * duration)

        return cls(
            format_id=format_id,
            resolution=resolution,
            fps=format_info.get("fps") or 30.0,
            language=format_info.get("language"),
            filesize=filesize,
        )


class VideoDTO(BaseContentDTO):
    """Data Transfer Object for Video."""

    path: Path | None = None
    thumbnail: str | Path | None = None
    thumbnail_url: str | None = None

    channel: str | None = None
    channel_id: str | None = None
    channel_url: str | None = None
    description: str | None = None

    duration: int | None = None
    width: int | None = None
    height: int | None = None
    ext: str | None = None
    season: str | None = None
    translation: str | None = None
    episode: str | None = None
    direct_download_url: str | None = None
    filename: str | None = None

    formats: list[FormatDTO] = Field(default_factory=list)

    @property
    def unique_formats(self) -> dict[str, list[FormatDTO]]:
        """
        Group available formats by a unique display label (e.g., '1080p').

        This helps in creating a clean UI where one button can represent
        multiple underlying formats (e.g., same resolution with different languages).

        :return: A dictionary mapping a label to a list of matching FormatDTOs.
        """
        grouped: dict[str, list[FormatDTO]] = defaultdict(list)
        for fmt in self.formats:
            if "audio only" not in fmt.resolution:
                grouped[fmt.label].append(fmt)
        sorted_grouped = sorted(
            grouped.items(),
            key=lambda item: item[1][0].height if item[1] else 0,
            reverse=True,
        )
        return dict(sorted_grouped)

    def get_formats_by_label(self, label: str) -> list[FormatDTO]:
        """
        Get a list of available formats by label.

        :param label: The label to get available formats for.
        :return: A list of available formats.
        """
        return self.unique_formats.get(label) or []

    @property
    def unique_labels(self) -> list[str]:
        """
        Return a set of unique labels (e.g., '1080p', '720p', etc).

        :return: A set of unique labels.
        """
        return list(self.unique_formats.keys())

    @property
    def title_html(self) -> str:
        """
        Return the title of the video formatted as html.

        :return: html string
        """
        if self.quality is not None and self.quality != "best":
            qualities = {}
            for key, formats in self.unique_formats.items():
                for fmt in formats:
                    qualities[fmt.format_id] = key
            if qualities:
                quality = f"[{qualities.get(self.quality,'')}]".replace("p", "")
            else:
                quality = f"[{self.quality}]".replace("p", "")
        else:
            quality = ""
        if self.channel and self.channel_url:
            title_html = (
                f'<b>{self.title}\u00A0<a href="{self.url}">{quality or '→'}</a></b>'
                f"\n\n"
                f"#{self.channel.replace(' ','_')}"
                f'<a href="{self.channel_url}">\u00A0→</a>'
                f"\n"
            )
        elif self.title:
            if quality:
                title_html = (
                    f'<b>{self.title}\u00A0<a href="{self.url}">{quality}\n</a></b>'
                )
            else:
                title_html = f'<b>{self.title}\u00A0<a href="{self.url}">→\n</a></b>'

            title_html += f"› {self.season}\n" if self.season else ""  # noqa RUF001
            title_html += f"› {self.episode}\n" if self.episode else ""  # noqa RUF001
            title_html += (
                f"› {self.translation}\n" if self.translation else ""  # noqa RUF001
            )

        else:
            title_html = f"{self.url}\n"
        return title_html

    def get_format_by_id(self, format_id: str) -> FormatDTO | None:
        """
        Get a FormatDTO by its ID.

        :param format_id: The ID of the format to get.
        :return: A FormatDTO instance.
        """
        return next(
            (fmt for fmt in self.formats if fmt.format_id == format_id),
            None,
        )

    @classmethod
    def get_video_dimensions_ffmpeg(
        cls,
        file_path: Path,
    ) -> tuple[int | None, int | None]:
        """
        Get video dimensions (width and height) using ffmpeg probe.

        Args:
            file_path (Path): Path to the video file.

        Returns:
            tuple: A tuple (width, height) as integers if successful,
                   otherwise (None, None)
        """
        try:
            if not file_path.exists():
                return None, None
            probe = ffmpeg.probe(str(file_path))
            video_stream = next(
                (
                    stream
                    for stream in probe["streams"]
                    if stream["codec_type"] == "video"
                ),
                None,
            )
            if video_stream:
                w = int(video_stream["width"])
                h = int(video_stream["height"])
                return w, h
        except Exception as e:
            logging.warning(f"Ошибка: {e}")
        return None, None

    @classmethod
    def from_yt_dlp(
        cls,
        info: dict[str, Any],
        quality: str = "best",
        file_path: Path | None = None,
        extract_direct_links: bool = False,
    ) -> "VideoDTO":
        """
        Create a VideoDTO instance from a yt-dlp info dictionary.

        :param info: The dictionary from yt_dlp.extract_info.
        :param quality: The quality of the video.
        :param file_path: The path to the downloaded video file.
        :param extract_direct_links: Whether to extract direct links.
        :return: A VideoDTO instance.
        """
        if not file_path:
            file_path = Path("dummy")

        direct_download_url = info.get("url") if extract_direct_links else None
        title = info.get("fulltitle") or info.get("title")
        channel = info.get("channel", "")
        channel_id = info.get("uploader_id", "")
        channel_url = info.get("uploader_url", "")
        duration = info.get("duration")

        available_formats = []
        for format_info in info.get("formats", []):
            vcodec = format_info.get("vcodec")
            acodec = format_info.get("acodec")
            if vcodec == "none":
                continue
            if acodec == "none":
                continue
            dto = FormatDTO.from_yt_dlp(format_info, duration)
            if not dto:
                continue

            if dto.filesize and dto.filesize > MAX_FILE_SIZE_BYTES:
                logging.info(
                    "Skipping format %s due to size limit: %s > %s",
                    dto.format_id,
                    dto.filesize,
                    MAX_FILE_SIZE_BYTES,
                )
                continue

            available_formats.append(dto)

        unique_formats = list(
            {(f.resolution, f.language): f for f in available_formats}.values(),
        )
        _width = int(w) if (w := info.get("width")) else None
        _height = int(h) if (h := info.get("height")) else None
        if file_path and (_width is None or _height is None):
            _width, _height = cls.get_video_dimensions_ffmpeg(file_path)
        return cls(
            path=file_path,
            thumbnail_url=info.get("thumbnail"),
            title=title,
            filename=title,
            channel=channel,
            channel_id=channel_id,
            channel_url=channel_url,
            direct_download_url=direct_download_url,
            url=info.get("original_url"),
            source_id=info.get("id"),
            width=_width,
            height=_height,
            quality=quality,
            formats=unique_formats,
            duration=int(duration) if duration else None,
            ext=info.get("ext"),
        )

    @classmethod
    def from_tikwm(
        cls,
        data: "TikWMData",
        url: str,
        quality: str = "default",
    ) -> "VideoDTO":
        """
        Create a VideoDTO instance from TikWM API data.

        :param data: The 'data' object from the TikWM API response.
        :param url: The original URL provided by the user.
        :param quality: The quality identifier for this video.
        :return: A populated VideoDTO instance.
        """
        return cls(
            direct_download_url=data.play,
            thumbnail_url=data.cover,
            title=data.title,
            filename=data.title,
            description=data.author_name,
            url=url,
            source_id=data.id,
            duration=data.duration,
            quality=quality,
        )

    @classmethod
    def from_instaloader(
        cls,
        item: Post | StoryItem | PostSidecarNode,
        url: str,
        source_id: str,
        caption: str | None = None,
    ) -> "VideoDTO":
        """Creates a VideoDTO from an Instaloader Post or StoryItem."""
        duration: int | None = None
        direct_download_url: str | None = None

        if item.video_url:
            direct_download_url = item.video_url

        if isinstance(item, Post) and item.is_video:
            duration = int(item.video_duration) if item.video_duration else None

        thumbnail_url = getattr(item, "url", getattr(item, "display_url", None))

        title = caption or getattr(item, "caption", None)

        return cls(
            url=url,
            source_id=source_id,
            title=title,
            filename=title,
            duration=duration,
            direct_download_url=direct_download_url,
            thumbnail_url=thumbnail_url,
        )

    @classmethod
    def from_kinovod(
        cls,
        video_dto: "VideoDTO",
        videotheatre_dto: "VideoTheatreDTO",
        resolution: Resolution,
    ) -> "VideoDTO":
        """Creates a VideoDTO from Kinovod fsm data."""
        url = resolution.url
        season_label = videotheatre_dto.season_label
        episode_label = videotheatre_dto.episode_label

        if resolution.metadata["type"] == "film":
            season_label = ""
            episode_label = ""

        title = videotheatre_dto.title

        if video_dto.path:
            (video_dto.width, video_dto.height) = cls.get_video_dimensions_ffmpeg(
                video_dto.path,
            )

        return cls(
            path=video_dto.path,
            source_id=videotheatre_dto.source_id,
            url=url,
            width=video_dto.width,
            height=video_dto.height,
            ext=video_dto.ext,
            title=title,
            filename=(title or "") + (videotheatre_dto.suffix or ""),
            quality=videotheatre_dto.quality_real,
            thumbnail_url=videotheatre_dto.thumbnail_url,
            season=season_label or "",
            translation=videotheatre_dto.translation_name or "",
            episode=episode_label or "",
        )


class PhotoDTO(BaseContentDTO):
    """Data Transfer Object for Photo."""

    path: Path | None = None
    media_url: str | None = None

    @classmethod
    def from_tikwm(
        cls,
        image_url: str,
        data: "TikWMData",
        resolution_url: str,
    ) -> "PhotoDTO":
        """Create a PhotoDTO instance from TikWM API data."""
        return cls(
            media_url=image_url,
            url=resolution_url,
            title=data.title,
            source_id=data.id,
        )

    @classmethod
    def from_vk_api(
        cls,
        photo_data: dict[str, Any],
        resolution_url: str,
    ) -> Optional["PhotoDTO"]:
        """
        Create PhotoDTO from VK API photo object.

        Automatically selects the best quality size.
        """
        sizes = photo_data.get("sizes", [])
        if not sizes:
            return None

        type_priority = {"w": 10, "z": 9, "y": 8, "x": 7, "m": 5, "s": 1}
        sorted_sizes = sorted(
            sizes,
            key=lambda s: (
                s.get("width", 0),
                type_priority.get(str(s.get("type", "")), 0),
            ),
            reverse=True,
        )
        best_url = sorted_sizes[0].get("url")

        if not best_url:
            return None

        return cls(
            media_url=best_url,
            url=resolution_url,
            source_id=str(photo_data.get("id", "")),
        )

    @classmethod
    def from_instaloader(
        cls,
        item: Post | StoryItem | PostSidecarNode,
        url: str,
        source_id: str,
        caption: str | None = None,
    ) -> "PhotoDTO":
        """
        Creates a PhotoDTO from Instaloader metadata WITHOUT a local file path.

        This is used for direct URL downloads.
        """
        media_url = getattr(item, "url", getattr(item, "display_url", None))

        return cls(
            url=url,
            source_id=source_id,
            title=caption or getattr(item, "caption", None),
            media_url=media_url,
        )


class AudioDTO(BaseContentDTO):
    """Data Transfer Object for Audio."""

    path: str | Path | None = None
    media_url: str | None = None
    duration: int | None = None
    artist: str | None = None
    track: str | None = None
    track_url: str | None = None
    album_url: str | None = None
    thumbnail_url: str | None = None
    thumbnail: str | Path | None = None

    direct_download_url: str | None = None

    @classmethod
    def from_tikwm(cls, data: "TikWMData", resolution_url: str) -> "AudioDTO | None":
        """Create an AudioDTO instance from TikWM API data."""
        if not data.music:
            return None

        return cls(
            media_url=data.music,
            title=cls._get_audio_title(data, resolution_url),
            duration=data.duration,
            url=resolution_url,
            source_id=data.id,
        )

    @staticmethod
    def _get_audio_title(data: "TikWMData", resolution_url: str) -> str:
        """Generate a meaningful title for the audio track."""
        url_match = re.search(r"/([\w-]+)/?$", resolution_url)
        base_name = url_match.group(1) if url_match else (data.title or data.id)
        # Remove invalid characters for most filesystems
        safe_name = re.sub(r'[\\/*?:"<>|]', "", base_name)
        # Truncate to avoid "Filename too long" errors
        return safe_name[:150].strip() or str(uuid.uuid4())

    @classmethod
    def from_vk_api(
        cls,
        audio_data: dict[str, Any],
        resolution_url: str,
    ) -> Optional["AudioDTO"]:
        """Create AudioDTO from VK API audio object."""
        owner_id = audio_data.get("owner_id")
        aid = audio_data.get("id")

        if not owner_id or not aid:
            return None

        source_id = f"{owner_id}_{aid}"
        audio_url = audio_data.get("url")

        if not audio_url:
            # URL может отсутствовать из-за ограничений правообладателя
            return None

        artist = audio_data.get("artist", "Unknown")
        title = audio_data.get("title", "Track")
        full_title = f"{artist} - {title}"

        return cls(
            media_url=audio_url,
            url=resolution_url,
            title=full_title,
            duration=audio_data.get("duration"),
            source_id=source_id,
        )

    @classmethod
    def from_yandexmusic(
        cls,
        audio_data: dict[str, Any],
        resolution_url: str,
    ) -> Optional["AudioDTO"]:
        """Create AudioDTO from yandex music audio object."""
        source_id = audio_data.get("id")

        if not source_id:
            return None

        duration = audio_data.get("duration")
        if isinstance(duration, float):
            duration = int(duration)

        audio_url = audio_data.get("url", "")

        track_url = audio_data.get("original_url", "")
        album_url = track_url.split("/track")[0]
        track = audio_data.get("track")
        entries = audio_data.get("entries", [])
        if entries:
            thumbnail_url = entries[0].get("thumbnail")
            artist = entries[0].get("artist")
        else:
            thumbnail_url = audio_data.get("thumbnail")
            artist = audio_data.get("artist")
        title = f"{artist} — {track}"
        thumbnail_url = thumbnail_url.replace("/orig", "/300x300")
        return cls(
            media_url=audio_url,
            url=resolution_url,
            title=title,
            duration=duration,
            source_id=source_id,
            artist=artist,
            track=track,
            track_url=track_url,
            album_url=album_url,
            thumbnail_url=thumbnail_url,
        )

    @classmethod
    def from_yandmatic(
        cls,
        track: TrackType,
        audio_url: str,
        resolution_url: str,
        album_id: Optional[str] = None,
    ) -> Optional["AudioDTO"]:
        """Create AudioDTO from yndmatic audio object."""
        if track.id is None:
            return None
        title = f"{track.artists_names} — {track.title}"
        thumbnail_url = None
        # check for thumbnail in albums if available, it may differ from track thumb
        if album_id and track.albums:
            for album in track.albums:
                if str(album.id) == str(album_id):
                    if album.cover_uri:
                        thumbnail_url = album.cover_uri
                    break
        # take default thumbnail from track model
        if thumbnail_url is None:
            thumbnail_url = track.cover_uri
        if thumbnail_url is not None:
            thumbnail_url = thumbnail_url.replace("%%", "300x300")
            thumbnail_url = (
                thumbnail_url
                if thumbnail_url.startswith("http")
                else "https://" + thumbnail_url
            )

        if "/track/" in resolution_url:
            album_url = resolution_url.split("/track/")[0]
        else:
            album_url = resolution_url
        return cls(
            media_url=audio_url,
            url=resolution_url,
            title=title,
            duration=int(track.duration_ms / 1000) if track.duration_ms else None,
            source_id=str(track.id),
            artist=track.artists_names,
            track=track.title,
            track_url=resolution_url,
            album_url=album_url,
            thumbnail_url=thumbnail_url,
        )

    @property
    def title_html(self) -> Optional[str]:
        """Make html title string."""
        track_url = self.track_url
        if track_url:
            _title_html = f"<a href='{self.track_url}'>{self.track}</a>"
        else:
            _title_html = f"<a href='{self.url}'>{self.title}</a>"
        return _title_html


class PhotoListDTO(BaseContentDTO):
    """Data Transfer Object for a list of photos (slideshow)."""

    photos: list[PhotoDTO]
    audio: AudioDTO | None = None

    @classmethod
    def from_tikwm(cls, data: "TikWMData", resolution_url: str) -> "PhotoListDTO":
        """
        Create a PhotoListDTO instance from TikWM API data.

        This method encapsulates the logic for building the entire slideshow object.
        """
        photos = []
        if data.images:
            photos = [
                PhotoDTO.from_tikwm(
                    image_url=img_url,
                    data=data,
                    resolution_url=resolution_url,
                )
                for img_url in data.images
            ]

        audio = AudioDTO.from_tikwm(data=data, resolution_url=resolution_url)

        return cls(
            photos=photos,
            audio=audio,
            source_id=data.id,
            url=resolution_url,
            title=data.title,
        )


class VtTranslations(BaseModel):
    """Data Transfer Object for translation in video theatries."""

    named_translations: dict[str, Any] = {}
    from_episode_translations: dict[str, Any] = {}

    @classmethod
    def from_dict(cls, translation_dict: dict[str, Any]) -> "VtTranslations":
        """Create VtTranslations instance from a dictionary."""
        return cls(
            named_translations=translation_dict["named_translations"],
            from_episode_translations=translation_dict["from_episode_translations"],
        )


class VideoTheatreDTO(BaseContentDTO):
    """Data Transfer Object for Video from online theatries."""

    suffix: Optional[str] = None
    thumbnail_url: Optional[str] = None
    perevod_from_html: Optional[str] = None
    proxy: Optional[str] = None
    raw_data: Optional[str] = None
    info_dict: dict[str, Any] = {}
    qualities: list[str] = []
    quality: Optional[str] = None
    quality_real: Optional[str] = None
    seasons: list[dict[str, Any]] = [{}]
    selected_season: dict[str, Any] = {}
    episodes: list[dict[str, Any]] = [{}]
    selected_episode: dict[str, Any] = {}
    translations: dict[str, Any] = {}
    translation_names: VtTranslations = VtTranslations()
    translation_name: Optional[str] = None
    season_label: Optional[str] = None
    translation_label: Optional[str] = None
    episode_label: Optional[str] = None

    @classmethod
    def from_raw_data(
        cls,
        raw_data: str,
        resolution_url: str,
        title: str,
        thumbnail_url: str,
        proxy: str,
        perevod_from_html: Optional[str] = None,
    ) -> "VideoTheatreDTO":
        """
        Create a VideoTheatreDTO from raw scraping data.

        This factory method is used when building the DTO from directly scraped
        HTML/JSON data before any user interaction (seasons/qualities selection).

        Args:
            raw_data: Raw scraped data (typically JSON string containing video info)
            resolution_url: URL pointing to the video resolution/playlist
            title: Title of the video content
            thumbnail_url: URL of the video thumbnail image
            proxy: Proxy server URL to use for requests
            perevod_from_html: Optional translation info extracted from HTML

        Returns:
            VideoTheatreDTO: Populated DTO instance with source_id derived from URL
        """
        source_id = resolution_url.split("/")[-1]
        return cls(
            title=title,
            url=resolution_url,
            source_id=source_id,
            thumbnail_url=thumbnail_url,
            perevod_from_html=perevod_from_html,
            proxy=proxy,
            raw_data=raw_data,
        )

    @classmethod
    def from_fsm_data(
        cls,
        fsm_data: dict[str, Any],
        resolution_url: str,
    ) -> "VideoTheatreDTO":
        """
        Create a VideoTheatreDTO from FSM context data.

        This factory method reconstructs the DTO after user selections have been
        made in an FSM workflow (e.g., chosen season, episode, quality, translation).

        The method extracts nested data from the FSM context, builds a unique
        source_id by appending suffixes from user selections, and populates
        all video navigation fields.

        Args:
            fsm_data: Dictionary from FSM context containing:
                - quality_label: User-selected quality (e.g., '1080p')
                - season_label: User-selected season (e.g., 'Season 1')
                - translation_label: User-selected translation/voiceover
                - episode_label: User-selected episode (e.g., 'Episode 1')
                - info_dict: JSON string containing nested structure with:
                    - title: Video title
                    - thumbnail_url: Thumbnail URL
                    - perevod_from_html: Translation info
                    - videotheatre_dto: Contains proxy and original info_dict
                        with seasons and translations data
            resolution_url: URL pointing to the video resolution/playlist

        Returns:
            VideoTheatreDTO: Populated DTO instance with all selection fields
                            and a source_id that includes user choices as suffix.

        """
        quality_label = fsm_data.get("quality_label", "")
        season_label = fsm_data.get("season_label", "")
        translation_label = fsm_data.get("translation_label", "")
        episode_label = fsm_data.get("episode_label", "")

        info_dict_fsm = json.loads(fsm_data.get("info_dict", "{}"))
        title = info_dict_fsm["title"]
        thumbnail_url = info_dict_fsm["thumbnail"]
        perevod_from_html = info_dict_fsm["videotheatre_dto"]["perevod_from_html"]
        proxy = info_dict_fsm["videotheatre_dto"]["proxy"]

        info_dict = info_dict_fsm["videotheatre_dto"]["info_dict"]
        seasons = info_dict["seasons"]

        selected_season = get_selected_season_or_episode(seasons, season_label)
        episodes = selected_season["folder"]
        selected_episode = get_selected_season_or_episode(episodes, episode_label)

        translations = selected_episode["file"].get(quality_label, {})
        quality_real = quality_label
        if not translations:
            qualities = info_dict["qualities"]
            qualities.remove(quality_label)
            qualities.reverse()
            for quality in info_dict["qualities"]:
                translations = selected_episode["file"].get(quality, {})
                if translations:
                    quality_real = quality
                    break

        translation_names = VtTranslations.from_dict(info_dict["translations"])
        translation_name = translation_names.named_translations.get(translation_label)
        if translation_name is None:
            translation_name = translation_names.from_episode_translations.get(
                translation_label,
                "",
            )

        suffix = ""
        if season_label:
            suffix += "_" + season_label.split(" ")[0]
        if episode_label:
            suffix += "_" + episode_label.split(" ")[0]

        if translation_label:
            suffix += "_" + translation_label.split(" ")[0]

        source_id = resolution_url.split("/")[-1]

        if "," in perevod_from_html:
            perevod_from_html = ""

        return cls(
            title=title,
            source_id=source_id + suffix,
            suffix=suffix,
            url=resolution_url,
            thumbnail_url=thumbnail_url,
            perevod_from_html=perevod_from_html,
            proxy=proxy,
            quality=quality_label,
            quality_real=quality_real,
            qualities=info_dict["qualities"],
            seasons=seasons,
            selected_season=selected_season,
            episodes=episodes,
            selected_episode=selected_episode,
            translations=translations,
            translation_name=translation_name,
            translation_names=translation_names,
            season_label=season_label,
            translation_label=translation_label,
            episode_label=episode_label,
        )


def get_selected_season_or_episode(
    video_items: list[dict[str, Any]],
    item_label: str | None,
) -> dict[str, Any]:
    """Return season or episode by its label."""
    if item_label:
        result = next(item for item in video_items if item["title"] == item_label)
    else:
        result = video_items[0]

    return result


CacheableDTO = Union[VideoDTO, PhotoDTO, AudioDTO, PhotoListDTO, VideoTheatreDTO]


class CacheDTO(BaseModel):
    """
    Data Transfer Object for creating a video cache entry.

    This is the data contract for the VideoCacheDAO.create method.
    """

    source: SourceEnum
    source_id: str
    file_id: str
    file_unique_id: str
    quality: str
    meta_data: VideoDTO | PhotoDTO | AudioDTO | PhotoListDTO | VideoTheatreDTO

    @classmethod
    def from_telegram_object(
        cls,
        source: SourceEnum,
        telegram_video: Union["TgVideo", "TgAudio"],
        content_dto: VideoDTO | PhotoDTO | AudioDTO | PhotoListDTO,
        quality: str | None,
    ) -> Optional["CacheDTO"]:
        """
        Create a CacheDTO instance from a telegram object and content DTO.

        :param source: The source of the content.
        :param telegram_video: The Video object from aiogram after sending.
        :param content_dto: The original DTO of the content.
        :param quality: The quality of the content.
        :return: A CacheDTO instance or None if not possible.
        """
        source_id = getattr(content_dto, "source_id", None)
        if not source_id:
            logging.warning(
                "Cannot create cache: source_id not found in content DTO.",
            )
            return None

        return cls(
            source=source,
            source_id=source_id,
            quality=quality or "best",
            meta_data=content_dto,
            file_id=telegram_video.file_id,
            file_unique_id=telegram_video.file_unique_id,
        )

    @classmethod
    def from_dto_object(
        cls,
        source: SourceEnum,
        content_dto: VideoTheatreDTO,
    ) -> Optional["CacheDTO"]:
        """
        Create a CacheDTO instance from a content DTO object.

        :param source: The source of the content.
        :param content_dto: The original DTO of the content.
        :return: A CacheDTO instance or None if not possible.
        """
        source_id = getattr(content_dto, "source_id", None)
        if not source_id:
            logging.warning(
                "Cannot create cache: source_id not found in content DTO.",
            )
            return None

        return cls(
            source=source,
            source_id=source_id,
            quality="best",
            meta_data=content_dto,
            file_id=str(uuid.uuid4()),
            file_unique_id=str(uuid.uuid4()),
        )


class TikWMAuthor(BaseModel):
    """Data Transfer Object for TikWM."""

    unique_id: str | None = None
    nickname: str | None = None


class TikWMData(BaseModel):
    """Pydantic model for the 'data' part of the TikWM API response."""

    id: str
    title: str | None = None
    duration: int = 0
    play: str | None = None
    music: str | None = None
    images: list[str] | None = None
    cover: str | None = None
    author: TikWMAuthor | None = None

    @property
    def author_name(self) -> str | None:
        """
        Get author's name or None.

        :return: Author name or None.
        """
        if not self.author:
            return None
        return self.author.nickname or self.author.unique_id


class TikWMResponse(BaseModel):
    """Pydantic model for the full TikWM API response."""

    code: int
    msg: str
    data: TikWMData | None = None
