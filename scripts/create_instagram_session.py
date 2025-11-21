import logging
import sys
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import instaloader

PROJECT_ROOT_FOR_PATH = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT_FOR_PATH))

from saver_backend.settings import settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def ensure_session_for_account(login: str) -> bool:
    """
    Ensures a valid Instaloader session exists for a given account.

    It first tries to load an existing session. If that fails or the file
    doesn't exist, it attempts to create a new session from a corresponding
    cookies.txt file.
    """
    try:
        project_root = Path(__file__).parent.parent.resolve()
        cookies_dir = project_root / "cookies" / "instagram_instaloader"
        cookies_file = cookies_dir / f"{login}.txt"
        session_file = cookies_dir / f"{login}.session"

        loader = instaloader.Instaloader(fatal_status_codes=[400, 403, 429])

        if session_file.exists():
            try:
                logging.info("Loading existing session for '%s'...", login)
                loader.load_session_from_file(
                    username=login,
                    filename=str(session_file),
                )
                loader.test_login()
                logging.info("Session for '%s' is valid.", login)
                return True
            except Exception:
                logging.info(
                    "Existing session for '%s' is invalid. Recreating...",
                    login,
                )

        if not cookies_file.exists():
            logging.warning(
                "Cookies file %s not found. Skipping session creation.",
                cookies_file,
            )
            # This is not a failure if cookies are not provided for account
            return True

        logging.info("Importing cookies from Netscape file: %s", cookies_file)
        jar = MozillaCookieJar()
        jar.load(str(cookies_file), ignore_discard=True, ignore_expires=True)
        loader.context._session.cookies = jar  # noqa: SLF001

        logging.info("Verifying session validity for user '%s'...", login)
        loader.context.username = login
        loader.test_login()
        logging.info("Login check for '%s' successful.", loader.context.username)

        loader.save_session_to_file(str(session_file))
        logging.info("Session successfully saved to: %s", session_file)

        return True
    except Exception:
        logging.exception("CRITICAL ERROR for account '%s':", login)
        return False


def main() -> None:
    """Main function to process all accounts."""
    logging.info("Starting Instaloader session creation/validation...")
    login = settings.instagram_account.split(":")[0]

    if not ensure_session_for_account(login):
        logging.critical("Failed to create or validate session.")
        sys.exit(1)

    logging.info("Instaloader session initialization complete.")


if __name__ == "__main__":
    main()
