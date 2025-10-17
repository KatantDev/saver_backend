from saver_backend.telegram_bot.handlers.download import download_router
from saver_backend.telegram_bot.handlers.exceptions import exception_router
from saver_backend.telegram_bot.handlers.start import start_router
from saver_backend.telegram_bot.handlers.subscribe import subscribe_router
from saver_backend.telegram_bot.handlers.vk import vk_router

routers = [
    start_router,
    subscribe_router,
    vk_router, 
    download_router,
    exception_router,
]
