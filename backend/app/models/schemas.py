from pydantic import BaseModel
from typing import Optional


class CookieImportRequest(BaseModel):
    cookie_string: str


class CookieStatusResponse(BaseModel):
    valid: bool
    last_updated: Optional[str] = None
    cookie_count: int = 0
    platform: str = "xueqiu"
