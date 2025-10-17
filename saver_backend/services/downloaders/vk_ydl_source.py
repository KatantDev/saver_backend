import logging
from typing import Any, ClassVar, Dict, TYPE_CHECKING

from yt_dlp import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController

if TYPE_CHECKING:
    from saver_backend.services.downloaders.schema import VideoDTO
    from saver_backend.services.telegram.bot_controller import TelegramBotController


class VkYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from VK through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.VK
    COOKIES: ClassVar[bool] = False

    def __init__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._yt_dlp.params["format"] = "best"
        self._yt_dlp.params["writethumbnail"] = True
        
        if self._telegram_bot_controller is None:
            self._message_id = None
        
        self._download_directory.mkdir(parents=True, exist_ok=True)

    def _process_percent(self, percent: int) -> None:
        """
        Process message - simplified version for local testing.

        :param percent: Percent of the video.
        """
        if self._telegram_bot_controller is None:
            logging.info(f"VK Download progress: {percent}%")
            return
        
        super()._process_percent(percent)

    def _send_finish_message(
        self,
        video: "VideoDTO",
    ) -> None:
        """
        Send finish message - simplified version for local testing.

        :param video: Video.
        """
        if self._telegram_bot_controller is None:
            logging.info(f"VK Download finished: {video.path}")
            if not self._filename:
                self._filename = Path(video.path)
            return
        
        super()._send_finish_message(video)

    async def get_video_info(self, url: str) -> Dict[str, Any] | None:
        """
        Get video information without downloading.

        :param url: URL of the video.
        :return: Dictionary with video information.
        """
        try:
            logging.info(f"Getting VK video info for URL: {url}")
            result = await self._get_video_info(url)
            logging.info(f"VK video info retrieved successfully: {result is not None}")
            return result
        except DownloadError as error:
            logging.error(f"VK DownloadError: {error.msg}")
            if "No video formats found" in error.msg:
                return None
            raise error
        except Exception as e:
            logging.error(f"VK unexpected error in get_video_info: {e}", exc_info=True)
            raise e

    async def get_available_formats(self, url: str) -> list[Dict[str, Any]] | None:
        """
        Get available video formats for quality selection.

        :param url: URL of the video.
        :return: List of available formats.
        """
        try:
            logging.info(f"Getting VK available formats for URL: {url}")
            info = await self._get_video_info(url)
            if not info:
                logging.warning("No video info retrieved")
                return None
            if 'formats' not in info:
                logging.warning("No formats in video info")
                return None
            
            logging.info(f"Found {len(info['formats'])} total formats")
            
            video_formats = []
            for fmt in info['formats']:
                if fmt.get('vcodec') != 'none' and fmt.get('height'):
                    video_formats.append({
                        'format_id': fmt.get('format_id'),
                        'height': fmt.get('height'),
                        'width': fmt.get('width'),
                        'ext': fmt.get('ext'),
                        'filesize': fmt.get('filesize'),
                        'quality': f"{fmt.get('height')}p" if fmt.get('height') else 'unknown'
                    })
            
            logging.info(f"Filtered to {len(video_formats)} video formats")
            
            video_formats.sort(key=lambda x: x.get('height', 0), reverse=True)
            return video_formats
        except Exception as e:
            logging.error(f"Error getting VK formats: {e}", exc_info=True)
            return None

    async def download_video(self) -> None:
        """
        Asynchronously downloads a video from VK.

        :return: Dictionary with information about the downloaded file.
        """
        selected_quality = self._resolution.metadata.get('quality', 'best')
        
        if selected_quality != 'best':
            formats = await self.get_available_formats(self._resolution.url)
            if formats:
                for fmt in formats:
                    if fmt['quality'] == selected_quality:
                        self._yt_dlp.params["format"] = fmt['format_id']
                        break
                else:
                    self._yt_dlp.params["format"] = "best"
            else:
                self._yt_dlp.params["format"] = "best"
        else:
            self._yt_dlp.params["format"] = "best"

        video_info = await self.get_video_info(url=self._resolution.url)

        if video_info is None:
            logging.error(
                "%s | Error getting video information (%s)",
                self.SOURCE,
                self._resolution.url,
            )
            return

        logging.info(
            "%s | Starting video download: %s (quality: %s)",
            self.SOURCE,
            self._resolution.url,
            selected_quality,
        )

        await self._download_video(url_list=[self._resolution.url])
