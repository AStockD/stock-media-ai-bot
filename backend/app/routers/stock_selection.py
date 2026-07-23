import hashlib
import logging
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_current_user
from app.config import ASTOCKD_POSTER_API_TOKEN, ASTOCKD_POSTER_API_URL, POSTER_CACHE_DIR
from app.services.llm_content_service import llm_content_service
from app.services.stock_selection_service import stock_selection_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stock-selection", tags=["stock-selection"])


@router.get("/strategies")
async def list_strategies(user: dict = Depends(get_current_user)):
    return {"strategies": stock_selection_service.get_strategies()}


@router.get("/records")
async def get_records(
    source: str = Query(..., description="Strategy source identifier"),
    user: dict = Depends(get_current_user),
):
    records = stock_selection_service.get_latest_records(source)
    return {"source": source, "records": records, "count": len(records)}


@router.post("/analyze")
async def analyze_stock(
    body: dict,
    user: dict = Depends(get_current_user),
):
    query = body.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    stock_name = body.get("stock_name", "")
    raw_summary, summary = stock_selection_service.analyze_query(query, stock_name)
    return {"summary": summary, "raw_summary": raw_summary}


async def _generate_poster(title: str, question: str, answer: str, qr_url: str) -> str | None:
    """Call poster API and return poster URL."""
    headers = {"Content-Type": "application/json"}
    if ASTOCKD_POSTER_API_TOKEN:
        headers["Authorization"] = f"Bearer {ASTOCKD_POSTER_API_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                ASTOCKD_POSTER_API_URL,
                json={"title": title, "question": question, "answer": answer, "qr_url": qr_url},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 200 and data.get("data", {}).get("url"):
                return data["data"]["url"]
    except Exception as e:
        logger.error(f"Poster generation failed: {e}")
    return None


@router.post("/optimize-content")
async def optimize_content(
    body: dict,
    user: dict = Depends(get_current_user),
):
    summary = body.get("summary", "")
    if not summary:
        raise HTTPException(status_code=400, detail="summary is required")
    stock_name = body.get("stock_name", "")
    trend = body.get("trend", "auto")
    raw_summary = body.get("raw_summary", "")

    # Extract title for poster
    title_match = re.match(r"^([^\n]+\n【[^\n]+】)", summary)
    title = title_match.group(1).replace("\n", " ") if title_match else f"{stock_name}股票分析"

    # Derive cache key the same way as LLM service
    body_text = summary
    if title_match:
        body_text = summary[len(title_match.group(1)):].strip()
    disclaimer_match = re.search(r"(\*\(免责申明[^\)]+\)\*?)\s*$", summary)
    if disclaimer_match:
        body_text = body_text[: -len(disclaimer_match.group(1))].strip()

    # Check cache
    cached = llm_content_service.get_cached(body_text, trend)
    poster_url = None
    local_image_path = None
    if cached:
        poster_url = cached[1]
        local_image_path = cached[2]
        if poster_url:
            logger.info(f"Poster cache hit for '{stock_name}'")

    optimized = llm_content_service.optimize_content(summary, stock_name, trend)

    # Generate poster and cache locally if not cached
    if not poster_url and raw_summary:
        poster_url = await _generate_poster(
            title=title,
            question=f"分析一下{stock_name}这只股票",
            answer=raw_summary,
            qr_url="https://www.astockd.com",
        )
        if poster_url:
            # Download poster to local cache
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(poster_url)
                    if resp.status_code == 200:
                        ext = ".png"
                        local_path = POSTER_CACHE_DIR / f"{hashlib.sha256(body_text.encode()).hexdigest()[:16]}_{trend}{ext}"
                        local_path.write_bytes(resp.content)
                        local_image_path = str(local_path)
                        logger.info(f"Poster cached locally: {local_image_path} ({len(resp.content)} bytes)")
            except Exception as e:
                logger.error(f"Failed to cache poster locally: {e}")

            llm_content_service.set_cache(body_text, trend, optimized, poster_url, local_image_path)
            logger.info(f"Poster generated and cached for '{stock_name}'")

    return {
        "optimized_summary": optimized,
        "poster_url": poster_url,
        "poster_local_path": local_image_path,
    }


@router.get("/kline")
async def get_kline(
    code: str = Query(..., description="Stock code, e.g. SH.600519"),
    user: dict = Depends(get_current_user),
):
    parts = code.split(".")
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="Invalid code format")
    prefix, num = parts
    secid = f"1.{num}" if prefix == "SH" else f"0.{num}"

    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&klt=101&fqt=1&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        f"&ut=b2884a393a59ad64002292a3e90d46a5&beg=20250101&end=20991231"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://finance.eastmoney.com/",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    klines = data.get("data", {}).get("klines", []) if data.get("data") else []
    return {"klines": klines}
