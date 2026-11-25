"""Workarounds for the VK extractor of yt-dlp 2026.10.26.

1. ``sub.get('title', '.srt').split('.')`` fails with «'NoneType' object has no
   attribute 'split'» when VK returns a subtitle track whose title is null. On
   prod on 21-23.11 that was 19 failed requests. The answer of VK is fixed up
   before the extractor reads it, so the video downloads.
2. A video that VK only embeds from ok.ru comes without a player of its own.
   The extractor looks for the ok.ru frame in one place, does not find it and
   crashes with «KeyError('params')». The link is in the answer, so it is
   reported in an error the bot can read.
3. A video that VK embeds from YouTube is marked «player.type = youtube». The
   extractor would go to YouTube through the Russian proxy of the VK source
   (29 s on dev before the bot could hand the video over); the link is
   reported the same way at once.
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
YOUTUBE_ID_RE = re.compile(r"^[\w-]{11}$")
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


def embedded_video(payload: Any) -> str | None:
    """
    Find the video of another site behind a VK page that only embeds it.

    :param payload: Parsed answer of VK to «show this video».
    :return: Link to the video on YouTube or ok.ru, None when VK plays the
        video itself.
    """
    options = payload[-1] if isinstance(payload, list) and payload else None
    if not isinstance(options, dict) or "mvData" not in options:
        return None
    player = options.get("player")
    params = player.get("params") if isinstance(player, dict) else None
    if isinstance(player, dict) and params:
        first = params[0] if isinstance(params, list) else None
        video_id = str(first.get("extra_data") or "") if isinstance(first, dict) else ""
        if player.get("type") == "youtube" and YOUTUBE_ID_RE.match(video_id):
            return f"https://youtu.be/{video_id}"
        return None
    match = OK_EMBED_RE.search(json.dumps(payload))
    return f"https://ok.ru/videoembed/{match.group(1)}" if match else None


def apply_vk_subtitle_patch() -> None:
    """Wrap the extractor's payload loader once per process."""
    original = VKBaseIE._download_payload  # noqa: SLF001
    if getattr(original, _PATCH_MARK, False):
        return

    def _download_payload(self: Any, *args: Any, **kwargs: Any) -> Any:
        payload = fix_subtitle_titles(original(self, *args, **kwargs))
        link = embedded_video(payload)
        if link:
            raise ExtractorError(HOSTED_ELSEWHERE_MARK + link, expected=True)
        return payload

    setattr(_download_payload, _PATCH_MARK, True)
    VKBaseIE._download_payload = _download_payload  # noqa: SLF001
