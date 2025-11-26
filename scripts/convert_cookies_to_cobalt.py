import json
import logging
from pathlib import Path
from typing import Any, Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_FILE = BASE_DIR / "cookies" / "instagram_cobalt" / "cookies.json"

# Mapping: Browser Domain -> Cobalt Service Name
# Cobalt uses internal service names, not domains.
DOMAIN_TO_SERVICE = {
    "instagram.com": "instagram",
}


def get_service_name(domain: str) -> str | None:
    """
    Resolve the Cobalt service name from a cookie domain.

    :param domain: The domain string from the cookie (e.g., ".instagram.com").
    :return: The internal Cobalt service name or None if not supported.
    """
    clean_domain = domain.lstrip(".")
    for d_key, service in DOMAIN_TO_SERVICE.items():
        if clean_domain == d_key or clean_domain.endswith(f".{d_key}"):
            return service
    return None


def _parse_raw_cookies(raw_data: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """
    Parse raw cookie objects and group them by service.

    :param raw_data: List of cookie dictionaries from browser export.
    :return: Dictionary where key is service name and value is list of "key=val" strings
    """
    service_cookie_parts: Dict[str, List[str]] = {}

    for cookie in raw_data:
        if not isinstance(cookie, dict):
            continue

        domain = cookie.get("domain", "")
        name = cookie.get("name", "")
        value = cookie.get("value", "")

        if not domain or not name:
            continue

        service = get_service_name(domain)
        if not service:
            continue

        if service not in service_cookie_parts:
            service_cookie_parts[service] = []

        # Append "key=value"
        service_cookie_parts[service].append(f"{name}={value}")

    return service_cookie_parts


def _format_cobalt_output(
    service_cookie_parts: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    """
    Format grouped cookies into Cobalt's final structure.

    Cobalt expects: { "service": [ "long_cookie_string_for_session_1" ] }

    :param service_cookie_parts: Dictionary of separated cookie parts.
    :return: Final dictionary for JSON dump.
    """
    final_output: Dict[str, List[str]] = {}

    for service, parts in service_cookie_parts.items():
        # Join all cookie parts with "; " to form one valid HTTP Cookie header
        full_cookie_string = "; ".join(parts)
        # Cobalt expects a list of strings (allowing multiple accounts).
        # We provide one account (one string).
        final_output[service] = [full_cookie_string]

    return final_output


def convert_browser_cookies_to_cobalt() -> None:
    """
    Convert standard browser JSON cookies to Cobalt format.

    Orchestrates the reading, backup, parsing, formatting, and saving process.
    """
    if not INPUT_FILE.exists():
        logging.error(f"Input file not found: {INPUT_FILE}")
        return

    try:
        # 1. Read Input
        with INPUT_FILE.open("r", encoding="utf-8") as f:
            raw_data: Any = json.load(f)

        # 2. Validate Input Format
        if not isinstance(raw_data, list):
            logging.error(
                "Invalid format: Input must be a JSON Array (list of objects). "
                "If the file is already a Dictionary, it might already be converted.",
            )
            return

        logging.info("Standard JSON Array detected. Starting conversion...")

        # 3. Process Logic
        grouped_parts = _parse_raw_cookies(raw_data)
        final_output = _format_cobalt_output(grouped_parts)

        if not final_output:
            logging.warning("No supported cookies found.")
            return

        # 4. Save Output
        with INPUT_FILE.open("w", encoding="utf-8") as f:
            json.dump(final_output, f, indent=4)

        logging.info("✅ Conversion successful!")
        logging.info(f"Services saved: {list(final_output.keys())}")
        logging.info(f"File saved to: {INPUT_FILE}")

    except Exception as e:
        logging.exception(f"An error occurred during conversion: {e}")


if __name__ == "__main__":
    convert_browser_cookies_to_cobalt()
