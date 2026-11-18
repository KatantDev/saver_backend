from aiogram.types import Video

# Telegram shows a video it got without a frame size as a 320x320 square
UNKNOWN_SIZE_SQUARE = (320, 320)


class BrokenCachedVideoError(Exception):
    """A cached file_id plays as a 320x320 square or without duration; drop it."""


def is_broken_video(
    video: Video | None,
    source_size: tuple[int | None, int | None] | None = None,
) -> bool:
    """
    Whether Telegram received a video without its metadata.

    A file uploaded without width, height and duration is shown by Telegram as a
    320x320 square with a zero duration. A file uploaded with a duration but no
    frame size (TikTok over 20 MB before 11.11) is a 320x320 square too. Such a
    message is useless to cache. A real 320x320 video is kept when the source
    states that size.

    :param video: Video of the sent message.
    :param source_size: (width, height) the source reported, if known.
    :return: True if the video has no duration or a frame size Telegram made up.
    """
    if video is None:
        return False
    if not video.duration:
        return True
    return (video.width, video.height) == UNKNOWN_SIZE_SQUARE and (
        source_size != UNKNOWN_SIZE_SQUARE
    )
