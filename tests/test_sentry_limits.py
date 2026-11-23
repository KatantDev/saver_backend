from typing import Any

from saver_backend.services.sentry_limits import (
    MAX_EVENT_BYTES,
    event_size,
    shrink_event,
)


def _event(var: str) -> dict[str, Any]:
    return {
        "message": "download failed",
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "HTTP Error 403: Forbidden",
                    "stacktrace": {
                        "frames": [
                            {"function": "fetch", "vars": {"page": var}},
                            {"function": "run", "vars": {"page": var}},
                        ],
                    },
                },
            ],
        },
        "breadcrumbs": {"values": [{"message": "GET https://example.com"}]},
    }


def test_small_event_is_sent_as_is() -> None:
    """A usual event keeps its local variables and breadcrumbs."""
    event = _event("short")

    sent = shrink_event(event, {})

    frames = sent["exception"]["values"][0]["stacktrace"]["frames"]
    assert frames[0]["vars"] == {"page": "short"}
    assert "breadcrumbs" in sent
    assert "tags" not in sent


def test_big_event_loses_variables_but_keeps_the_error() -> None:
    """Prod since 17.11: events over 1 MB were rejected and no error was stored."""
    event = _event("x" * MAX_EVENT_BYTES)

    sent = shrink_event(event, {})

    error = sent["exception"]["values"][0]
    assert error["value"] == "HTTP Error 403: Forbidden"
    assert [f["function"] for f in error["stacktrace"]["frames"]] == ["fetch", "run"]
    assert all("vars" not in f for f in error["stacktrace"]["frames"])
    assert "breadcrumbs" not in sent
    assert sent["tags"] == {"event_shrunk": "true"}
    assert event_size(sent) < MAX_EVENT_BYTES
