import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field
from sentry_sdk import capture_exception

from saver_backend.entities.enums import SourceEnum

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
        Generate a user-friendly label for this format.

        :return: A string label like '1080p' or '720p (audio)'.
        """
        try:
            parts = self.resolution.split("x")
            if len(parts) == 2:
                height = int(parts[1])
                label = f"{height}p"
                if self.language:
                    label += f" ({self.language})"
                return label
        except (ValueError, IndexError) as e:
            capture_exception(e)
        return self.resolution

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

    path: str | Path
    thumbnail: str | Path | None = None
    thumbnail_url: str | None = None

    title: str | None = None
    url: str | None = None
    source_id: str | None = None

    duration: int | None = None
    width: int | None = None
    height: int | None = None
    quality: str | None = None

    formats: list[FormatDTO] = Field(default_factory=list)

    @property
    def unique_formats_by_label(self) -> dict[str, list[FormatDTO]]:
        """
        Group available formats by a unique display label (e.g., '1080p').

        This helps in creating a clean UI where one button can represent
        multiple underlying formats (e.g., same resolution with different languages).

        :return: A dictionary mapping a label to a list of matching FormatDTOs.
        """
        grouped: dict[str, list[FormatDTO]] = {}
        for fmt in self.formats:
            try:
                parts = fmt.resolution.split("x")
                if len(parts) == 2:
                    height = int(parts[1])
                    label = f"{height}p"
                    if label not in grouped:
                        grouped[label] = []
                    grouped[label].append(fmt)
            except (ValueError, IndexError):
                continue
        return grouped

    @classmethod
    def from_yt_dlp(
        cls,
        info: dict[str, Any],
        file_path: Path,
        thumbnail_path: Path | None,
    ) -> "VideoDTO":
        """
        Create a VideoDTO instance from a yt-dlp info dictionary.

        :param info: The dictionary from yt_dlp.extract_info.
        :param file_path: The path to the downloaded video file.
        :param thumbnail_path: The path to the downloaded thumbnail.
        :return: A VideoDTO instance.
        """
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
            if dto:
                available_formats.append(dto)

        unique_formats = []
        seen_combinations = set()
        for fmt in available_formats:
            combination = (fmt.resolution, fmt.language)
            if combination not in seen_combinations:
                unique_formats.append(fmt)
                seen_combinations.add(combination)

        return cls(
            path=file_path,
            thumbnail=thumbnail_path,
            thumbnail_url=info.get("thumbnail"),
            title=title,
            url=info.get("original_url"),
            source_id=info.get("id"),
            width=int(w) if (w := info.get("width")) else None,
            height=int(h) if (h := info.get("height")) else None,
            quality="best",
            formats=unique_formats,
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
