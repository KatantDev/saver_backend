from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from saver_backend.entities.enums import SourceEnum


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

    @classmethod
    def from_yt_dlp_info(
        cls,
        info: dict[str, Any],
        file_path: Path,
        thumbnail_path: Path | None,
    ) -> VideoDTO:
        """
        Create a VideoDTO instance from a yt-dlp info dictionary.

        :param info: The dictionary from yt_dlp.extract_info.
        :param file_path: The path to the downloaded video file.
        :param thumbnail_path: The path to the downloaded thumbnail.
        :return: A VideoDTO instance.
        """
        title = info.get("title")

        quality = "best"

        return cls(
            path=file_path,
            thumbnail=thumbnail_path,
            title=title,
            url=info.get("original_url"),
            source_id=info.get("id"),
            width=int(w) if (w := info.get("width")) else None,
            height=int(h) if (h := info.get("height")) else None,
            quality=quality,
        )


class PhotoDTO(BaseModel):
    """Data Transfer Object for Photo."""

    path: str | Path
    title: str | None = None
    url: str | None = None
