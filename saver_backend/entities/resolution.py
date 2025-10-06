from typing import Any

from pydantic import BaseModel

from saver_backend.entities.enums import SourceEnum


class Resolution(BaseModel):
    """Resolution model for source of url."""

    source: SourceEnum
    url: str
    metadata: dict[str, Any] = {}
