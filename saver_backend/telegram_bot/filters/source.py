from typing import Any

from aiogram.filters import Filter
from aiogram.types import Message

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.resolver import SourceResolver


class SourceFilter(Filter):
    """Filter for source of the message."""

    def __init__(self, sources: list[SourceEnum]) -> None:
        self.sources = sources
        self.source_resolver = SourceResolver()

    async def __call__(self, message: Message) -> dict[str, Any] | bool:
        """
        Check if the source of the message is a valid source.

        :param message: Message.
        :return: True if the source of the message is a valid source, False otherwise.
        """
        if message.text is None:
            return False
        resolution = self.source_resolver.resolve(message.text)
        if resolution.source not in self.sources:
            return False
        return {"resolution": resolution}
