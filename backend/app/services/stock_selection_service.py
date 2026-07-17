"""Stock selection records service - query astockd database."""
import logging
from typing import Dict, List, Optional

from app.database import get_astockd_db

logger = logging.getLogger(__name__)

STRATEGIES = [
    {"id": "top_scored", "name": "高分选股"},
    {"id": "strategy_screener", "name": "策略选股"},
    {"id": "limitup_review", "name": "涨停复盘"},
    {"id": "market_review", "name": "市场复盘"},
]


class StockSelectionService:
    def get_strategies(self) -> List[Dict]:
        return STRATEGIES

    def get_latest_records(self, source: str) -> List[Dict]:
        with get_astockd_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT code, name, sector, selection_date, timestamp,
                              source, overall_score, sentiment_norm, tick_norm,
                              flow_norm, tech_norm, kline_norm, price, pct_change
                       FROM stock_selection_records
                       WHERE source = %s
                         AND selection_date = (
                             SELECT MAX(selection_date)
                             FROM stock_selection_records
                             WHERE source = %s
                         )
                       ORDER BY timestamp DESC""",
                    (source, source),
                )
                rows = cur.fetchall()
        results = []
        for row in rows:
            results.append({
                "code": row["code"],
                "name": row["name"],
                "sector": row["sector"] or "",
                "selection_date": str(row["selection_date"]),
                "timestamp": str(row["timestamp"]),
                "source": row["source"],
                "overall_score": float(row["overall_score"]),
                "sentiment_norm": float(row["sentiment_norm"]),
                "tick_norm": float(row["tick_norm"]),
                "flow_norm": float(row["flow_norm"]),
                "tech_norm": float(row["tech_norm"]),
                "kline_norm": float(row["kline_norm"]),
                "price": float(row["price"]),
                "pct_change": float(row["pct_change"]),
            })
        logger.info(f"Fetched {len(results)} records for source={source}")
        return results


stock_selection_service = StockSelectionService()
