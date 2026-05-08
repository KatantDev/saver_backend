import asyncio
import functools
import logging
from typing import Any, Awaitable, Callable, TypeAlias, TypeVar

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

T = TypeVar("T")

AsyncCallable: TypeAlias = Callable[..., Awaitable[T]]


def retry_on_telegram_timeout(
    exception_return: Any = None,
) -> Callable[[AsyncCallable[T]], AsyncCallable[T]]:
    """
    Decorator to retry a function once if a "Request timeout error" message occurs.

    :param exception_return: Value to return in case of exceptions other than
                             TelegramNetworkError with "Request timeout error".
    :return: Decorator function.
    """

    def decorator(func: AsyncCallable[T]) -> AsyncCallable[T]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return await func(*args, **kwargs)
            except TelegramNetworkError as e:
                if "Request timeout error" in e.message:
                    return await func(*args, **kwargs)
                if "too big for a video note" in e.message:
                    return exception_return
                raise
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                return await func(*args, **kwargs)
            except Exception as e:
                logging.error(f"Failed to send meme in func {func.__name__}: {e}")
                return exception_return

        return wrapper

    return decorator
