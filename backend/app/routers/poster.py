import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.config import ASTOCKD_POSTER_API_TOKEN, ASTOCKD_POSTER_API_URL

logger = logging.getLogger(__name__)

router = APIRouter()


class PosterGenerateRequest(BaseModel):
    title: str
    question: str
    answer: str
    qr_url: str


@router.post("/api/poster/generate")
async def generate_poster(
    req: PosterGenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """Proxy poster generation API to astockd.com"""
    headers = {"Content-Type": "application/json"}
    if ASTOCKD_POSTER_API_TOKEN:
        headers["Authorization"] = f"Bearer {ASTOCKD_POSTER_API_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                ASTOCKD_POSTER_API_URL,
                json={
                    "title": req.title,
                    "question": req.question,
                    "answer": req.answer,
                    "qr_url": req.qr_url,
                },
                headers=headers,
            )
            logger.info("Poster API response: %s %s", resp.status_code, resp.text[:200])
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("Poster API HTTP error: %s %s", e.response.status_code, e.response.text[:500])
        raise HTTPException(
            status_code=502,
            detail=f"Poster API error: {e.response.status_code} {e.response.text[:200]}",
        )
    except httpx.HTTPError as e:
        logger.error("Poster API error: %s", e)
        raise HTTPException(status_code=502, detail=f"Poster API error: {str(e)}")
