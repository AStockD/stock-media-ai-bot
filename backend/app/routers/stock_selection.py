import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_current_user
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


@router.post("/optimize-content")
async def optimize_content(
    body: dict,
    user: dict = Depends(get_current_user),
):
    summary = body.get("summary", "")
    if not summary:
        raise HTTPException(status_code=400, detail="summary is required")
    stock_name = body.get("stock_name", "")
    optimized = llm_content_service.optimize_content(summary, stock_name)
    return {"optimized_summary": optimized}


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
