from dataclasses import dataclass

from saver_backend.web.exceptions import ForbiddenException, UnauthorizedException


@dataclass
class InvalidWebhookSecretException(ForbiddenException):
    detail: str = "Invalid webhook secret"


@dataclass
class InvalidWebAppDataException(UnauthorizedException):
    detail: str = "Invalid webapp data (signature check failed)"
