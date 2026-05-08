from typing import Any

from aiogram.filters import Filter
from aiogram.types import InlineQuery, Message

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.resolver import SourceResolver


class SourceFilter(Filter):
    """Filter for source of the message."""

    def __init__(self, sources: list[SourceEnum]) -> None:
        self.sources = sources
        self.source_resolver = SourceResolver()

    async def __call__(
        self,
        event: Message | InlineQuery,
    ) -> dict[str, Any] | bool:
        """
        Check if the source of the message or query is a valid source.

        :param event: Message or InlineQuery object.
        :return: Dict with resolution if valid, False otherwise.
        """
        text = ""
        if isinstance(event, Message) and event.text:
            text = event.text
        elif isinstance(event, InlineQuery):
            text = event.query

        if not text:
            return False

        resolution = self.source_resolver.resolve(text)
        if resolution.source not in self.sources:
            return False

        return {"resolution": resolution}
