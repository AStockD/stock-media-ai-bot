"""Stock selection records service - calls stock-diagnosis-app API."""
import logging
from typing import Dict, List

import httpx

from app.config import STOCK_ANALYZE_URL, STOCK_ANALYZE_BEARER_TOKEN

logger = logging.getLogger(__name__)

STRATEGIES = [
    {"id": "top_scored", "name": "高分选股"},
    {"id": "strategy_screener", "name": "策略选股"},
    {"id": "limitup_review", "name": "涨停复盘"},
    {"id": "market_review", "name": "市场复盘"},
]


class StockSelectionService:
    def __init__(self):
        self._client = httpx.Client(
            base_url=STOCK_ANALYZE_URL,
            headers={
                "Authorization": f"Bearer {STOCK_ANALYZE_BEARER_TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "StockMediaAIBot/0.3",
            },
            timeout=15,
        )

    def get_strategies(self) -> List[Dict]:
        return STRATEGIES

    def get_latest_records(self, source: str) -> List[Dict]:
        body = {"source": source} if source else {}
        resp = self._client.post("/scores/selection-records", json=body)
        resp.raise_for_status()
        data = resp.json()
        records = []
        for r in data.get("records", []):
            records.append({
                "code": r["code"],
                "name": r["name"],
                "sector": r.get("sector", ""),
                "selection_date": r.get("selection_date", ""),
                "timestamp": r.get("timestamp", ""),
                "source": r["source"],
                "sub_sources": r.get("sub_sources", []),
                "overall_score": float(r.get("overall_score", 0)),
                "sentiment_norm": float(r.get("sentiment_norm", 0)),
                "tick_norm": float(r.get("tick_norm", 0)),
                "flow_norm": float(r.get("flow_norm", 0)),
                "tech_norm": float(r.get("tech_norm", 0)),
                "kline_norm": float(r.get("kline_norm", 0)),
                "price": float(r.get("price", 0)),
                "pct_change": float(r.get("pct_change", 0)),
            })
        logger.info(f"Fetched {len(records)} records for source={source} via API")
        return records


stock_selection_service = StockSelectionService()
