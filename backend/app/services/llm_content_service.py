"""LLM content optimization service for stock analysis posts."""
import logging

from openai import OpenAI

from app.config import LLM_API_URL, LLM_API_KEY, LLM_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位专业的股票分析师，擅长撰写客观中立的股票分析文章。你的文章风格特点：
1. 基于数据说话，保留所有客观数据（价格、指标、资金流向、技术信号等）
2. 有明确的分析倾向（看多或看空），但不会直接下结论如"建议买入"或"建议卖出"
3. 语气专业、客观，让读者自行判断
4. 结构清晰，先说主要观点，再补充风险提示"""

USER_PROMPT_TEMPLATE = """请基于以下股票分析内容，完成以下任务：

1. **判断方向**：根据数据判断应该看多还是看空
2. **优化文章**：按照以下结构重新组织内容

文章结构要求：
- **第一段（主要观点）**：总结支持你判断方向的数据和描述，调整语气使其符合判断方向，但不要直接下结论（不要出现"建议买入/卖出"、"强烈推荐"等）
- **第二段（风险提示）**：提醒与主要观点相反的数据或信号，作为风险提醒，但不要让人感觉到整体矛盾

写作要求：
- 保留所有原始数据（具体数字、百分比、指标名称等）
- 保持专业金融分析的叙述风格
- 不要出现 markdown 格式符号（如 ##、**、* 等）
- 不要添加原文没有的内容或数据
- 两段之间过渡自然，不要有矛盾感

原始分析内容：
{summary}

请直接输出优化后的文章内容（两段），不要输出其他内容。"""


class LLMContentService:
    def __init__(self):
        self._client = None
        self._model = None

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

    def optimize_content(self, summary: str, stock_name: str = "") -> str:
        """Use LLM to optimize content with bullish/bearish tone."""
        client, model = self._get_client()
        if not client or not model:
            logger.warning("LLM client not available, returning original content")
            return summary

        try:
            user_prompt = USER_PROMPT_TEMPLATE.format(summary=summary)
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
            logger.info(
                f"LLM optimization done for '{stock_name}': "
                f"{len(summary)} -> {len(optimized)} chars"
            )
            return optimized
        except Exception as e:
            logger.error(f"LLM optimization failed: {e}", exc_info=True)
            return summary


llm_content_service = LLMContentService()
