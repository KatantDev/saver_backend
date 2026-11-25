"""Workarounds for the VK extractor of yt-dlp 2026.10.26.

1. ``sub.get('title', '.srt').split('.')`` fails with «'NoneType' object has no
   attribute 'split'» when VK returns a subtitle track whose title is null. On
   prod on 21-23.11 that was 19 failed requests. The answer of VK is fixed up
   before the extractor reads it, so the video downloads.
2. A video that VK only embeds from ok.ru comes without a player of its own.
   The extractor looks for the ok.ru frame in one place, does not find it and
   crashes with «KeyError('params')». The link is in the answer, so it is
   reported in an error the bot can read.
"""

import json
import re
from typing import Any

from yt_dlp.extractor.vk import VKBaseIE
from yt_dlp.utils import ExtractorError

DEFAULT_SUBTITLE_TITLE = ".srt"
_PATCH_MARK = "_saver_subtitle_patch"
# Start of the error text that carries the link of the real host
HOSTED_ELSEWHERE_MARK = "hosted elsewhere: "
OK_EMBED_RE = re.compile(r"ok\.ru(?:\\*/)+videoembed(?:\\*/)+(\d+)")


def fix_subtitle_titles(payload: Any) -> Any:
    """
    Give every subtitle track a text title.

    :param payload: Parsed answer of VK, any nesting of dicts and lists.
    :return: The same object, changed in place.
    """
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            subs = node.get("subs")
            if isinstance(subs, list):
                for sub in subs:
                    if isinstance(sub, dict) and not isinstance(sub.get("title"), str):
                        sub["title"] = DEFAULT_SUBTITLE_TITLE
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return payload


def embedded_ok_video(payload: Any) -> str | None:
    """
    Find the ok.ru video behind a VK page that has no player of its own.

    :param payload: Parsed answer of VK to «show this video».
    :return: Link to the video on ok.ru, None when VK plays the video itself.
    """
    options = payload[-1] if isinstance(payload, list) and payload else None
    if not isinstance(options, dict) or "mvData" not in options:
        return None
    player = options.get("player")
    if isinstance(player, dict) and player.get("params"):
        return None
    match = OK_EMBED_RE.search(json.dumps(payload))
    return f"https://ok.ru/video/{match.group(1)}" if match else None


def apply_vk_subtitle_patch() -> None:
    """Wrap the extractor's payload loader once per process."""
    original = VKBaseIE._download_payload  # noqa: SLF001
    if getattr(original, _PATCH_MARK, False):
        return

    def _download_payload(self: Any, *args: Any, **kwargs: Any) -> Any:
        payload = fix_subtitle_titles(original(self, *args, **kwargs))
        link = embedded_ok_video(payload)
        if link:
            raise ExtractorError(HOSTED_ELSEWHERE_MARK + link, expected=True)
        return payload

    setattr(_download_payload, _PATCH_MARK, True)
    VKBaseIE._download_payload = _download_payload  # noqa: SLF001
