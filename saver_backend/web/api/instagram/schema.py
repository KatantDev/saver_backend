from pydantic import BaseModel


class SessionPushRequest(BaseModel):
    """Cookies of instagram.com from a browser where the account is logged in."""

    cookies: dict[str, str]


class SessionPushResponse(BaseModel):
    """Account whose session was saved."""

    username: str
