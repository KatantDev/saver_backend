import logging

import sentry_sdk
from fastapi import FastAPI
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.integrations.stdlib import StdlibIntegration

from saver_backend.log import configure_logging
from saver_backend.settings import settings
from saver_backend.web.api.router import api_router
from saver_backend.web.lifespan import lifespan_setup
from saver_backend.web.webhook.views import webhook_router


def get_app() -> FastAPI:
    """
    Get FastAPI application.

    This is the main constructor of an application.

    :return: application.
    """
    configure_logging()
    if settings.sentry_dsn:
        # Enables sentry integration.
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=settings.sentry_sample_rate,
            environment=settings.environment,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                LoggingIntegration(
                    level=logging.getLevelName(
                        settings.log_level.value,
                    ),
                    event_level=logging.ERROR,
                ),
                SqlalchemyIntegration(),
                StdlibIntegration(),
                RedisIntegration(),
            ],
            enable_tracing=True,
            profile_session_sample_rate=1.0,
            profile_lifecycle="trace",
        )
    # FastAPI now serializes data directly via Pydantic to JSON bytes when a
    # return type or response_model is set, which is faster than UJSONResponse
    # and doesn't need a custom default_response_class.
    app = FastAPI(
        title="saver_backend",
        version="0.0.1",
        lifespan=lifespan_setup,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # Main router for the API.
    app.include_router(router=api_router, prefix="/api")
    app.include_router(router=webhook_router, prefix="/api/webhook")

    return app
