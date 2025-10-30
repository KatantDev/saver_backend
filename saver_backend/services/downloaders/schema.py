import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.consts import MAX_FILE_SIZE_BYTES
from saver_backend.services.language_resolver import LanguageResolver

if TYPE_CHECKING:
    from aiogram.types import Video as TgVideo


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
        if not format_id or not resolution or "audio only" in resolution:
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
            fps=format_info.get("fps", 30.0),
            language=format_info.get("language"),
            filesize=filesize,
        )


class VideoDTO(BaseModel):
    """Data Transfer Object for Video."""

    path: str | Path | None = None
    thumbnail: str | Path | None = None
    thumbnail_url: str | None = None

    title: str | None = None
    url: str | None = None
    source_id: str | None = None

    duration: int | None = None
    width: int | None = None
    height: int | None = None
    quality: str | None = None

    direct_download_url: str | None = None

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
        duration = info.get("duration")

        available_formats = []
        for format_info in info.get("formats", []):
            vcodec = format_info.get("vcodec")
            acodec = format_info.get("acodec")
            if not vcodec or vcodec == "none":
                continue
            if not acodec or acodec == "none":
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

        return cls(
            path=file_path,
            thumbnail_url=info.get("thumbnail"),
            title=title,
            direct_download_url=direct_download_url,
            url=info.get("original_url"),
            source_id=info.get("id"),
            width=int(w) if (w := info.get("width")) else None,
            height=int(h) if (h := info.get("height")) else None,
            quality=quality,
            formats=unique_formats,
            duration=int(duration) if duration else None,
        )


class VideoCacheDTO(BaseModel):
    """
    Data Transfer Object for creating a video cache entry.

    This is the data contract for the VideoCacheDAO.create method.
    """

    source: SourceEnum
    source_id: str
    file_id: str
    file_unique_id: str
    quality: str
    meta_data: VideoDTO

    @classmethod
    def from_yt_dlp(
        cls,
        source: SourceEnum,
        telegram_video: "TgVideo",
        video: "VideoDTO",
    ) -> Optional["VideoCacheDTO"]:
        """
        Create a VideoCacheDTO instance from a yt-dlp info dictionary.

        :param source: The source of the video.
        :param telegram_video: The Video object from aiogram after sending.
        :param video: The VideoDTO instance.
        :return: A VideoCacheDTO instance.
        """
        if not video.source_id:
            logging.warning(
                "Cannot create video cache: source_id not found in video info.",
            )
            return None

        return cls(
            source=source,
            source_id=video.source_id,
            file_id=telegram_video.file_id,
            file_unique_id=telegram_video.file_unique_id,
            quality=video.quality or "best",
            meta_data=video,
        )


class PhotoDTO(BaseModel):
    """Data Transfer Object for Photo."""

    path: str | Path
    title: str | None = None
    url: str | None = None
