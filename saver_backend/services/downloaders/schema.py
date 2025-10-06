from pathlib import Path

from pydantic import BaseModel


class VideoDTO(BaseModel):
    """Data Transfer Object for Video."""

    path: str | Path
    title: str | None = None
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    thumbnail: str | Path | None = None
    url: str | None = None


class PhotoDTO(BaseModel):
    """Data Transfer Object for Photo."""

    path: str | Path
    title: str | None = None
    url: str | None = None
