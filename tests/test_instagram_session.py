from http.cookiejar import Cookie
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from instaloader.exceptions import LoginException, TwoFactorAuthRequiredException

from saver_backend.services.downloaders import instagram_session
from saver_backend.services.downloaders.instagram_session import (
    InstagramSessionError,
    is_login_required_error,
    refresh_session,
    totp_code,
)


def _cookie(name: str, value: str) -> Cookie:
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        ".instagram.com",
        True,
        True,
        "/",
        True,
        True,
        None,
        False,
        None,
        None,
        {},
    )


@pytest.fixture
def cookies_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point all session files to a temporary folder."""
    monkeypatch.setattr(instagram_session, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(
        instagram_session,
        "YDL_COOKIES_FILE",
        tmp_path / "instagram_ydl" / "cookies_session.txt",
    )
    monkeypatch.setattr(
        instagram_session,
        "LAST_LOGIN_FILE",
        tmp_path / "instagram_instaloader" / ".last_login",
    )
    monkeypatch.setattr(instagram_session.settings, "instagram_account", "bot:secret")
    monkeypatch.setattr(instagram_session.settings, "instagram_totp_secret", None)
    return tmp_path


def _loader(logged_in_as: str | None = None) -> MagicMock:
    loader = MagicMock()
    loader.test_login.return_value = logged_in_as
    loader.context._session.cookies = [_cookie("sessionid", "abc")]  # noqa: SLF001
    return loader


def test_totp_code_matches_rfc6238() -> None:
    """RFC 6238 SHA1 test vector, truncated to 6 digits."""
    assert totp_code("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", timestamp=59) == "287082"


def test_valid_session_is_kept(cookies_dir: Path) -> None:
    """A working session is not replaced and cookies are exported for yt-dlp."""
    session_file = cookies_dir / "instagram_instaloader" / "bot.session"
    session_file.parent.mkdir(parents=True)
    session_file.touch()
    loader = _loader(logged_in_as="bot")

    with patch.object(instagram_session, "_new_loader", return_value=loader):
        assert refresh_session() is True

    loader.login.assert_not_called()
    assert (
        "sessionid\tabc"
        in (cookies_dir / "instagram_ydl" / "cookies_session.txt").read_text()
    )


def test_expired_session_logs_in_again(cookies_dir: Path) -> None:
    """An expired session triggers a login and is saved."""
    session_file = cookies_dir / "instagram_instaloader" / "bot.session"
    session_file.parent.mkdir(parents=True)
    session_file.touch()
    loader = _loader(logged_in_as=None)

    with patch.object(instagram_session, "_new_loader", return_value=loader):
        assert refresh_session() is True

    loader.login.assert_called_once_with("bot", "secret")
    loader.save_session_to_file.assert_called_once_with(str(session_file))


def test_two_factor_code_is_generated(
    cookies_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2FA is passed with a code from the authenticator secret."""
    monkeypatch.setattr(
        instagram_session.settings,
        "instagram_totp_secret",
        "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
    )
    loader = _loader()
    loader.login.side_effect = TwoFactorAuthRequiredException("2fa")

    with patch.object(instagram_session, "_new_loader", return_value=loader):
        assert refresh_session() is True

    code = loader.two_factor_login.call_args.args[0]
    assert len(code) == 6
    assert code.isdigit()


def test_checkpoint_needs_a_person(cookies_dir: Path) -> None:
    """A checkpoint is reported instead of being retried."""
    loader = _loader()
    loader.login.side_effect = LoginException("Login: Checkpoint required.")

    with (
        patch.object(instagram_session, "_new_loader", return_value=loader),
        pytest.raises(InstagramSessionError),
    ):
        refresh_session()


def test_login_is_not_retried_within_an_hour(cookies_dir: Path) -> None:
    """A second login right after the first one is skipped."""
    loader = _loader()
    loader.login.side_effect = LoginException("Login: Checkpoint required.")

    with patch.object(instagram_session, "_new_loader", return_value=loader):
        with pytest.raises(InstagramSessionError):
            refresh_session()
        assert refresh_session() is False

    assert loader.login.call_count == 1


def test_not_configured_account_is_skipped(
    cookies_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing happens while the account is not set."""
    monkeypatch.setattr(
        instagram_session.settings,
        "instagram_account",
        "username:password",
    )
    with patch.object(instagram_session, "_new_loader") as new_loader:
        assert refresh_session() is False
    new_loader.assert_not_called()


def test_login_required_errors_are_detected() -> None:
    """yt-dlp messages that mean the session is gone."""
    assert is_login_required_error(
        Exception("ERROR: [Instagram] x: Instagram sent an empty media response."),
    )
    assert is_login_required_error(
        Exception(
            "Requested content is not available, rate-limit reached or login required"
        ),
    )
    assert not is_login_required_error(Exception("HTTP Error 404: Not Found"))
