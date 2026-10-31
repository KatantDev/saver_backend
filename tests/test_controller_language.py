import asyncio

import pytest

from saver_backend.services.telegram.bot_controller import TelegramBotController


@pytest.mark.anyio
async def test_parallel_requests_keep_their_own_language() -> None:
    """Two users handled at once by the shared controller keep their language."""
    controller = TelegramBotController.__new__(TelegramBotController)
    first_set = asyncio.Event()
    seen: dict[str, str | None] = {}

    async def russian_user() -> None:
        controller.language = "ru"
        controller.last_message_id = 1
        first_set.set()
        await asyncio.sleep(0.01)  # the other request runs meanwhile
        seen["ru"] = controller.language
        seen["ru_message"] = str(controller.last_message_id)

    async def english_user() -> None:
        await first_set.wait()
        controller.language = "en"
        controller.last_message_id = 2
        seen["en"] = controller.language
        seen["en_message"] = str(controller.last_message_id)

    await asyncio.gather(
        asyncio.create_task(russian_user()),
        asyncio.create_task(english_user()),
    )

    assert seen == {"ru": "ru", "ru_message": "1", "en": "en", "en_message": "2"}


@pytest.mark.anyio
async def test_language_is_empty_for_a_new_request() -> None:
    """A request that has not set a language does not inherit another one."""
    controller = TelegramBotController.__new__(TelegramBotController)

    async def other_request() -> None:
        controller.language = "ru"

    await asyncio.create_task(other_request())

    assert controller.language is None
    assert controller.last_message_id is None
