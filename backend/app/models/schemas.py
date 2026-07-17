from pydantic import BaseModel
from typing import Optional


class CookieImportRequest(BaseModel):
    cookie_string: str


class CookieStatusResponse(BaseModel):
    valid: bool
    last_updated: Optional[str] = None
    cookie_count: int = 0
    platform: str = "xueqiu"


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    user: dict


class UserInfo(BaseModel):
    id: int
    username: str
    role: str


class PlatformAccountInfo(BaseModel):
    platform: str
    account_name: Optional[str] = None
    is_valid: bool
    last_updated: Optional[str] = None
    cookie_count: int = 0
