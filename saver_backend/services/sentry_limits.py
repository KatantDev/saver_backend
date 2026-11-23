"""
Keep Sentry events small enough to be accepted.

Sentry rejects an event over 1 MB («too_large»). sentry-sdk 2.x no longer cuts
long values by default, so a local variable holding a downloaded page or a
yt-dlp answer goes into the event whole: one 400 KB page in one stack frame gave
a 408 KB event (measured on dev, 23.11.2026). From 17.11.2026 prod errors were
rejected this way and none reached Sentry.
"""

import json
from typing import Any

# Longest string kept from a local variable, message or request field.
MAX_VALUE_LENGTH = 2000
# Above this size the event is sent without local variables and breadcrumbs.
MAX_EVENT_BYTES = 500_000


def event_size(event: dict[str, Any]) -> int:
    """Size of the event as it is sent, in bytes."""
    return len(json.dumps(event, default=str).encode("utf-8"))


def shrink_event(event: Any, _hint: dict[str, Any]) -> Any:
    """
    Drop local variables and breadcrumbs from an event that is still too big.

    The error itself, its message and the stack trace are kept, so the event is
    accepted instead of being rejected whole.

    :param event: Event built by the SDK.
    :param _hint: Unused.
    :return: The event, smaller when it was over MAX_EVENT_BYTES.
    """
    if event_size(event) <= MAX_EVENT_BYTES:
        return event
    for group in ("exception", "threads"):
        for value in (event.get(group) or {}).get("values") or []:
            for frame in (value.get("stacktrace") or {}).get("frames") or []:
                frame.pop("vars", None)
    event.pop("breadcrumbs", None)
    event.setdefault("tags", {})["event_shrunk"] = "true"
    return event
