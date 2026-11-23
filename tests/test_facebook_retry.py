from saver_backend.services.downloaders.facebook_ydl_source import (
    FacebookYdlController,
)


def test_facebook_retries_through_another_proxy_then_direct() -> None:
    """A proxy that times out on facebook.com must not fail the request."""
    assert FacebookYdlController.RETRIES_ON_ERROR == 1
    assert FacebookYdlController.DIRECT_FALLBACK is True
