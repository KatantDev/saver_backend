from aiogram.types import Video


class BrokenCachedVideoError(Exception):
    """A cached file_id plays as a 320x320 clip with no duration; drop it."""


def is_broken_video(video: Video | None) -> bool:
    """
    Whether Telegram received a video without its metadata.

    A file uploaded without width, height and duration is shown by Telegram as a
    320x320 square with a zero duration; such a message is useless to cache.

    :param video: Video of the sent message.
    :return: True if the video has no duration.
    """
    return video is not None and not video.duration
