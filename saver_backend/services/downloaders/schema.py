import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel

from saver_backend.entities.enums import SourceEnum

if TYPE_CHECKING:
    from aiogram.types import Video as TgVideo


class FormatDTO(BaseModel):
    """Data Transfer Object for a specific video format."""

    format_id: str
    resolution: str
    fps: float = 30.0

    @classmethod
    def from_yt_dlp(cls, format_info: dict[str, Any]) -> Optional["FormatDTO"]:
        """
        Create a FormatDTO instance from a yt-dlp format info dictionary.

        :param format_info: The dictionary from yt_dlp.extract_info.
        :return: A FormatDTO instance.
        """
        logging.info(format_info)
        format_id = format_info.get("format_id")
        resolution = format_info.get("resolution")
        fps = format_info.get("fps", 30.0)
        if not format_id or not resolution:
            logging.warning("Cannot create format dto: %s", format_info)
            return None
        return cls(
            format_id=format_id,
            resolution=resolution,
            fps=fps,
        )


class VideoDTO(BaseModel):
    """Data Transfer Object for Video."""

    path: str | Path
    thumbnail: str | Path | None = None

    title: str | None = None
    url: str | None = None
    source_id: str | None = None

    duration: int | None = None
    width: int | None = None
    height: int | None = None
    quality: str | None = None

    formats: list[FormatDTO] = []

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
        title = info.get("title")
        quality = "best"

        available_formats = []
        for format_info in info.get("formats", []):
            vcodec = format_info.get("vcodec")
            acodec = format_info.get("acodec")
            if not vcodec or vcodec == "none":
                continue
            if not acodec or acodec == "none":
                continue

            dto = FormatDTO.from_yt_dlp(format_info)
            if dto:
                available_formats.append(dto)

        return cls(
            path=file_path,
            thumbnail=thumbnail_path,
            title=title,
            url=info.get("original_url"),
            source_id=info.get("id"),
            width=int(w) if (w := info.get("width")) else None,
            height=int(h) if (h := info.get("height")) else None,
            quality=quality,
            formats=available_formats,
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
            quality=video.quality,
            meta_data=video,
        )


class PhotoDTO(BaseModel):
    """Data Transfer Object for Photo."""

    path: str | Path
    title: str | None = None
    url: str | None = None
