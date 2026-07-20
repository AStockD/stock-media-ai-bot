import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

COOKIE_FILE = DATA_DIR / "cookies.json"
STORAGE_STATE_FILE = DATA_DIR / "storage_state.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

XUEQIU_BASE_URL = "https://xueqiu.com"
XUEQIU_LOGIN_URL = "https://xueqiu.com"

DB_HOST = os.getenv("DB_HOST", "10.0.0.135")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "smab")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "smab")
ASTOCKD_DB_NAME = os.getenv("ASTOCKD_DB_NAME", "astockd")

JWT_SECRET = os.getenv("JWT_SECRET", "smab-dev-secret-change-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 72

STOCK_ANALYZE_URL = os.getenv("STOCK_ANALYZE_URL", "http://10.0.0.135:8888")
STOCK_ANALYZE_BEARER_TOKEN = os.getenv(
    "STOCK_ANALYZE_BEARER_TOKEN", "astk_d8a7f2e6b4c9103591d4e8b7a6c5f3d2"
)

ASTOCKD_POSTER_API_URL = os.getenv(
    "ASTOCKD_POSTER_API_URL", "https://astockd.com/api/v1/operator/posters/generate"
)
ASTOCKD_POSTER_API_TOKEN = os.getenv("ASTOCKD_POSTER_API_TOKEN", "")

LLM_API_URL = os.getenv("LLM_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-plus")

POSTER_CACHE_DIR = DATA_DIR / "poster_cache"
POSTER_CACHE_DIR.mkdir(exist_ok=True)
