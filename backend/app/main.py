import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.platform import router as platform_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Stock Media AI Bot", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(platform_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "stock-media-ai-bot"}
