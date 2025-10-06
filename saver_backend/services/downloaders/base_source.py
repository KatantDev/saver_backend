from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Dict

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution

if TYPE_CHECKING:
    from saver_backend.services.telegram.bot_controller import TelegramBotController


class BaseSourceController(ABC):
    """Asynchronous controller for downloading videos from different sources."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.UNSUPPORTED

    def __init__(
        self,
        telegram_bot_controller: "TelegramBotController",
        telegram_id: int,
    ) -> None:
        self._telegram_bot_controller = telegram_bot_controller
        self._telegram_id = telegram_id

    @abstractmethod
    async def download_video(
        self,
        resolution: Resolution,
    ) -> Dict[str, Any] | None:
        """
        Download video.

        :param resolution: Resolution of the video.
        :return: Dictionary with video information.
        """
        raise NotImplementedError
