from dataclasses import dataclass

from fastapi import HTTPException, status


@dataclass
class DetailedHTTPException(HTTPException):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail: str = "Server error"


@dataclass
class ForbiddenException(DetailedHTTPException):
    status_code: int = status.HTTP_403_FORBIDDEN
    detail: str = "Forbidden"


@dataclass
class BadRequestException(DetailedHTTPException):
    status_code: int = status.HTTP_400_BAD_REQUEST
    detail: str = "Bad request"


@dataclass
class UnauthorizedException(DetailedHTTPException):
    status_code: int = status.HTTP_401_UNAUTHORIZED
    detail: str = "Unauthorized"


@dataclass
class NotFoundException(DetailedHTTPException):
    status_code: int = status.HTTP_404_NOT_FOUND
    detail: str = "Not found"


@dataclass
class UserNotFoundException(NotFoundException):
    detail: str = "User not found"
