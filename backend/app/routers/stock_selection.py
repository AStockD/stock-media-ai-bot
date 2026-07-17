import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_current_user
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
