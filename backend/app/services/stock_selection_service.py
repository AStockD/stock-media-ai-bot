"""Stock selection records service - calls stock-diagnosis-app API."""
import logging
import re
from datetime import date
from typing import Dict, List

import httpx

from app.config import STOCK_ANALYZE_URL, STOCK_ANALYZE_BEARER_TOKEN

logger = logging.getLogger(__name__)

STRATEGIES = [
    {"id": "top_scored", "name": "高分选股"},
    {"id": "strategy_screener", "name": "策略选股"},
    {"id": "limitup_review", "name": "涨停复盘"},
    {"id": "market_review", "name": "市场复盘"},
    {"id": "subscription_model", "name": "订阅模型"},
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

    def analyze_query(self, query: str, stock_name: str = "") -> str:
        resp = self._client.post("/analyze/query", json={"query": query}, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        summary = data.get("summary", "")

        if stock_name:
            today = date.today().strftime("%Y/%m/%d")
            new_title = f"## A股道股票解读每日分享 - {today} - {stock_name}\n\n*(免责申明: 本文解读内容均为 【A股道】AI 股票诊断生成，不构成投资建议，仅供参考！)*"
            summary = re.sub(r"^## [^\n]+", new_title, summary, count=1, flags=re.MULTILINE)

        summary = re.sub(
            r"^## 快速结论\n[\s\S]*?(?=^## |\Z)",
            "",
            summary,
            flags=re.MULTILINE,
        )

        summary = re.sub(
            r"^## 综合评分[^\n]*\n[\s\S]*?(?=^## |^> |^---|\Z)",
            "",
            summary,
            flags=re.MULTILINE,
        )

        summary = re.sub(
            r"^---\n+\s*免责申明[^\n]*\n+",
            "",
            summary,
            flags=re.MULTILINE,
        )

        summary = re.sub(
            r"\n?^## 斐波那契位置与策略\n[\s\S]*?(?=^## |\Z)",
            "",
            summary,
            flags=re.MULTILINE,
        )

        logger.info(f"Analyze query='{query}', summary length={len(summary)}")
        return summary

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
