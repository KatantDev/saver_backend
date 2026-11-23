"""Workaround for a crash inside yt-dlp's VK extractor.

yt-dlp 2026.10.26, vk.py: ``sub.get('title', '.srt').split('.')`` fails with
«'NoneType' object has no attribute 'split'» when VK returns a subtitle track
whose title is null. On prod on 21-23.11 that was 19 failed requests. The
answer of VK is fixed up before the extractor reads it, so the video downloads.
"""

from typing import Any

from yt_dlp.extractor.vk import VKBaseIE

DEFAULT_SUBTITLE_TITLE = ".srt"
_PATCH_MARK = "_saver_subtitle_patch"


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


def apply_vk_subtitle_patch() -> None:
    """Wrap the extractor's payload loader once per process."""
    original = VKBaseIE._download_payload  # noqa: SLF001
    if getattr(original, _PATCH_MARK, False):
        return

    def _download_payload(self: Any, *args: Any, **kwargs: Any) -> Any:
        return fix_subtitle_titles(original(self, *args, **kwargs))

    setattr(_download_payload, _PATCH_MARK, True)
    VKBaseIE._download_payload = _download_payload  # noqa: SLF001
