import os
import time
from http.cookiejar import Cookie
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from instaloader.exceptions import LoginException, TwoFactorAuthRequiredException

from saver_backend.services.downloaders import instagram_session
from saver_backend.services.downloaders.instagram_session import (
    InstagramSessionError,
    InstagramSessionRejectedError,
    is_login_required_error,
    refresh_session,
    save_pushed_session,
    totp_code,
)


def _cookie(name: str, value: str, domain: str = ".instagram.com") -> Cookie:
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        domain,
        bool(domain),
        domain.startswith("."),
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
    monkeypatch.setattr(
        instagram_session,
        "LOGIN_FAILED_FILE",
        tmp_path / "instagram_instaloader" / ".login_failed",
    )
    monkeypatch.setattr(
        instagram_session,
        "SESSION_RECEIVED_FILE",
        tmp_path / "instagram_instaloader" / ".session_received",
    )
    monkeypatch.setattr(instagram_session.settings, "instagram_account", "bot:secret")
    monkeypatch.setattr(instagram_session.settings, "instagram_totp_secret", None)
    monkeypatch.setattr(instagram_session.settings, "instagram_session_token", None)
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


def test_failed_login_pauses_retries_for_a_day(cookies_dir: Path) -> None:
    """After a failed login the bot waits a day, not an hour, before retrying."""
    loader = _loader()
    loader.login.side_effect = LoginException("Login: Checkpoint required.")
    last_login = cookies_dir / "instagram_instaloader" / ".last_login"
    failed = cookies_dir / "instagram_instaloader" / ".login_failed"

    with patch.object(instagram_session, "_new_loader", return_value=loader):
        with pytest.raises(InstagramSessionError):
            refresh_session()
        two_hours_ago = time.time() - 2 * 3600
        os.utime(last_login, (two_hours_ago, two_hours_ago))
        os.utime(failed, (two_hours_ago, two_hours_ago))
        assert refresh_session() is False

    assert loader.login.call_count == 1


def test_successful_login_clears_the_pause(cookies_dir: Path) -> None:
    """A login more than a day after the failure is tried, and success clears it."""
    failed = cookies_dir / "instagram_instaloader" / ".login_failed"
    failed.parent.mkdir(parents=True)
    failed.touch()
    day_ago = time.time() - 25 * 3600
    os.utime(failed, (day_ago, day_ago))
    loader = _loader()

    with patch.object(instagram_session, "_new_loader", return_value=loader):
        assert refresh_session() is True

    loader.login.assert_called_once()
    assert not failed.exists()


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


def test_cookies_without_domain_are_exported_for_instagram(cookies_dir: Path) -> None:
    """Cookies read from a session file get the instagram.com domain for yt-dlp."""
    session_file = cookies_dir / "instagram_instaloader" / "bot.session"
    session_file.parent.mkdir(parents=True)
    session_file.touch()
    loader = _loader(logged_in_as="bot")
    loader.context._session.cookies = [_cookie("sessionid", "abc", domain="")]  # noqa: SLF001

    with patch.object(instagram_session, "_new_loader", return_value=loader):
        assert refresh_session() is True

    exported = (cookies_dir / "instagram_ydl" / "cookies_session.txt").read_text()
    line = next(row for row in exported.splitlines() if "sessionid" in row)
    assert line.startswith(".instagram.com\tTRUE\t")


def test_pushed_session_is_saved(cookies_dir: Path) -> None:
    """A working session from a browser is saved for instaloader and yt-dlp."""
    failed = cookies_dir / "instagram_instaloader" / ".login_failed"
    failed.parent.mkdir(parents=True)
    failed.touch()
    loader = _loader(logged_in_as="bot")
    cookies = {"sessionid": "abc", "csrftoken": "token"}

    with patch.object(instagram_session, "_new_loader", return_value=loader):
        assert save_pushed_session(cookies) == "bot"

    loader.load_session.assert_called_once_with("bot", cookies)
    loader.save_session_to_file.assert_called_once_with(
        str(cookies_dir / "instagram_instaloader" / "bot.session"),
    )
    assert (cookies_dir / "instagram_ydl" / "cookies_session.txt").exists()
    assert (cookies_dir / "instagram_instaloader" / ".session_received").exists()
    assert not failed.exists()


@pytest.mark.parametrize(
    ("cookies", "logged_in_as"),
    [
        ({"csrftoken": "token"}, "bot"),
        ({"sessionid": "abc", "csrftoken": "token"}, None),
        ({"sessionid": "abc", "csrftoken": "token"}, "someone_else"),
    ],
    ids=["no sessionid", "dead session", "another account"],
)
def test_bad_pushed_session_is_rejected(
    cookies_dir: Path,
    cookies: dict[str, str],
    logged_in_as: str | None,
) -> None:
    """Nothing is saved unless the cookies are a working session of the account."""
    loader = _loader(logged_in_as=logged_in_as)

    with (
        patch.object(instagram_session, "_new_loader", return_value=loader),
        pytest.raises(InstagramSessionRejectedError),
    ):
        save_pushed_session(cookies)

    loader.save_session_to_file.assert_not_called()
    assert not (cookies_dir / "instagram_ydl" / "cookies_session.txt").exists()


def test_pushed_sessions_replace_password_login(
    cookies_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a session token set the bot asks a person once a day, never logs in."""
    monkeypatch.setattr(instagram_session.settings, "instagram_session_token", "t")
    session_file = cookies_dir / "instagram_instaloader" / "bot.session"
    session_file.parent.mkdir(parents=True)
    session_file.touch()
    loader = _loader(logged_in_as=None)

    with patch.object(instagram_session, "_new_loader", return_value=loader):
        with pytest.raises(InstagramSessionError):
            refresh_session()
        assert refresh_session() is False

    loader.login.assert_not_called()
