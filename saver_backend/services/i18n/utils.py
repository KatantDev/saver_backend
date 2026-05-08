from typing import Any

from aiogram.utils.i18n import I18n
from babel.support import LazyProxy


class CustomLazyProxy(LazyProxy):
    """Proxy for lazy translation strings."""

    def __getattr__(self, name: str) -> Any:
        if name == "__set_name__":
            return None
        return super().__getattr__(name)


def get_i18n() -> I18n:
    """
    Get the current I18n context.

    :return: I18n context
    """
    i18n = I18n.get_current(no_error=True)
    if i18n is None:
        raise LookupError("I18n context is not set")
    return i18n


def gettext(*args: Any, **kwargs: Any) -> str:
    """
    Translate a string.

    :param args: arguments for translation
    :param kwargs: keyword arguments for translation
    :return: translated string
    """
    return get_i18n().gettext(*args, **kwargs)


def lazy_gettext(*args: Any, **kwargs: Any) -> CustomLazyProxy:
    """
    Translate a string lazily.

    :param args: arguments for translation
    :param kwargs: keyword arguments for translation
    :return: lazy translated string
    """
    return CustomLazyProxy(gettext, *args, **kwargs, enable_cache=False)


ngettext = gettext
lazy_ngettext = lazy_gettext
