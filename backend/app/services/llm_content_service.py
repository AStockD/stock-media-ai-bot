"""LLM content optimization service for stock analysis posts."""
import hashlib
import logging
import re
import time
from pathlib import Path

from openai import OpenAI

from app.config import LLM_API_URL, LLM_API_KEY, LLM_MODEL

logger = logging.getLogger(__name__)

CACHE_TTL = 8 * 3600  # 8 hours

SYSTEM_PROMPT = """你是一位专业的股票分析师，擅长撰写客观中立的股票分析文章。你的文章风格特点：
1. 基于数据说话，保留所有客观数据（价格、指标、资金流向、技术信号等）
2. 有明确的分析倾向（看多或看空），但不会直接下结论如"建议买入"或"建议卖出"
3. 语气专业、客观，让读者自行判断
4. 结构清晰，先说主要观点，再补充风险提示"""

USER_PROMPT_TEMPLATE = """请基于以下股票分析内容，完成以下任务：

1. **方向设定**：{trend_instruction}
2. **优化文章**：按照以下结构重新组织内容

文章结构要求：
- **第一段（主要观点）**：总结支持{trend_name}方向的数据和描述，调整语气使其符合判断方向，但不要直接下结论（不要出现"建议买入/卖出"、"强烈推荐"等）
- **第二段（风险提示）**：提醒与{trend_name}方向相反的数据或信号，作为风险提醒，但不要让人感觉到整体矛盾

写作要求：
- 保留所有原始数据（具体数字、百分比、指标名称等）
- 保持专业金融分析的叙述风格
- 不要出现 markdown 格式符号（如 ##、**、* 等）
- 不要添加原文没有的内容或数据
- 两段之间过渡自然，不要有矛盾感
- 每段中的多个要点请使用换行和 - 符号逐条罗列，不要用分号连接成长句。例如：
  - 要点一：xxx
  - 要点二：xxx

原始分析内容：
{summary}

请直接输出优化后的文章内容（两段），不要输出其他内容。"""


class LLMContentService:
    def __init__(self):
        self._client = None
        self._model = None
        self._cache: dict[str, tuple[float, str, str | None, str | None]] = {}  # key -> (ts, content, poster_url, local_path)

    def _cache_key(self, body: str, trend: str) -> str:
        h = hashlib.sha256(body.encode()).hexdigest()[:16]
        return f"{h}:{trend}"

    def _cleanup_cache(self):
        now = time.time()
        expired = []
        for k, (ts, _, _, local_path) in self._cache.items():
            if now - ts > CACHE_TTL:
                expired.append((k, local_path))
        for k, local_path in expired:
            del self._cache[k]
            if local_path:
                try:
                    p = Path(local_path)
                    if p.exists():
                        p.unlink()
                        logger.info(f"Deleted expired poster cache file: {local_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete poster cache file {local_path}: {e}")
        if expired:
            logger.info(f"LLM cache cleaned: {len(expired)} expired entries")

    def get_cached(self, body: str, trend: str) -> tuple[str, str | None, str | None] | None:
        """Return (optimized_content, poster_url, local_image_path) from cache, or None."""
        self._cleanup_cache()
        key = self._cache_key(body, trend)
        if key in self._cache:
            ts, content, poster_url, local_path = self._cache[key]
            logger.info(f"LLM cache hit (age={int(time.time()-ts)}s)")
            return content, poster_url, local_path
        return None

    def set_cache(self, body: str, trend: str, content: str, poster_url: str | None = None, local_image_path: str | None = None):
        key = self._cache_key(body, trend)
        self._cache[key] = (time.time(), content, poster_url, local_image_path)

    def _get_client(self) -> tuple[OpenAI | None, str | None]:
        if self._client is None:
            try:
                self._client = OpenAI(
                    api_key=LLM_API_KEY,
                    base_url=LLM_API_URL,
                    max_retries=2,
                    timeout=60,
                )
                self._model = LLM_MODEL
                logger.info(f"LLM client initialized: {LLM_API_URL} / {LLM_MODEL}")
            except Exception as e:
                logger.error(f"Failed to initialize LLM client: {e}")
                return None, None
        return self._client, self._model

    def optimize_content(self, summary: str, stock_name: str = "", trend: str = "auto") -> str:
        """Use LLM to optimize content with bullish/bearish tone."""
        client, model = self._get_client()
        if not client or not model:
            logger.warning("LLM client not available, returning original content")
            return summary

        # Determine trend instruction
        if trend == "bullish":
            trend_instruction = "请按照看多的方向进行分析"
            trend_name = "看多"
        elif trend == "bearish":
            trend_instruction = "请按照看空的方向进行分析"
            trend_name = "看空"
        else:
            trend_instruction = "根据数据判断应该看多还是看空"
            trend_name = "你判断的"

        # Extract title (first two lines: title line + date line)
        title_match = re.match(r"^([^\n]+\n【[^\n]+】)", summary)
        title = title_match.group(1) if title_match else ""

        # Extract disclaimer (last part starting with 免责申明)
        disclaimer_match = re.search(r"(\*\(免责申明[^\)]+\)\*?)\s*$", summary)
        disclaimer = disclaimer_match.group(1) if disclaimer_match else ""

        # Get body content (without title and disclaimer)
        body = summary
        if title:
            body = body[len(title):].strip()
        if disclaimer:
            body = body[: -len(disclaimer)].strip()

        # Check cache
        cached = self.get_cached(body, trend)
        if cached:
            return cached[0]

        try:
            user_prompt = USER_PROMPT_TEMPLATE.format(
                summary=body,
                trend_instruction=trend_instruction,
                trend_name=trend_name,
            )
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=2000,
            )
            optimized = response.choices[0].message.content.strip()

            # Restore title and disclaimer
            if title:
                optimized = title + "\n\n" + optimized
            if disclaimer:
                optimized = optimized + "\n\n" + disclaimer

            # Store in cache (poster_url set separately by router)
            self.set_cache(body, trend, optimized)

            logger.info(
                f"LLM optimization done for '{stock_name}' (trend={trend}): "
                f"{len(summary)} -> {len(optimized)} chars"
            )
            return optimized
        except Exception as e:
            logger.error(f"LLM optimization failed: {e}", exc_info=True)
            return summary


llm_content_service = LLMContentService()
