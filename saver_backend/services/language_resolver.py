import logging

from babel import Locale
from flag import UnsupportedFlag, flag_safe

LANG_CODE_COUNTRY = {
    "en": "US",
    "zh": "CN",
    "hi": "IN",
    "iw": "IL",
    "ja": "JP",
    "ko": "KR",
    "te": "IN",
    "uk": "ua",
    "fil": "ph",
}


class LanguageResolver:
    """Language Resolver."""

    def __init__(self, language: str) -> None:
        self.language = language

        separated = self.language.split("-", maxsplit=1)
        self.code = separated[0].lower()
        self.territory = separated[1] if len(separated) == 2 else None

    @property
    def language_name(self) -> str | None:
        """
        Get the language name from the language code.

        :return: A string for the language code, e.g., "English".
        """
        try:
            locale = Locale.parse(self.code, sep="_")
            if self.territory:
                locale.territory = self.territory
            if self.code == "zh":
                locale.script = self.territory
            return locale.get_display_name()
        except (ValueError, TypeError, AttributeError) as e:
            logging.exception(e)
            return None

    @property
    def flag_name(self) -> str | None:
        """
        Get language flag name.

        :return: language flag name
        """
        country_code = LANG_CODE_COUNTRY.get(self.code) or self.code
        try:
            return flag_safe(country_code)
        except UnsupportedFlag:
            logging.error(f"Unsupported flag '{country_code} ({self.language_name})'")
            return ""

    @property
    def display_name(self) -> str:
        """
        Generate a user-friendly button text for language selection.

        :return: A string for the button, e.g., "English".
        """
        language = self.language_name

        if language is None:
            language_text = self.language.upper()
        else:
            language_text = language.capitalize()
        return f"{self.flag_name} {language_text}"
