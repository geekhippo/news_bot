"""
AI-модуль для генерации описаний новостей через OpenRouter API.
"""
import asyncio
import os
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-3.1-flash-lite-preview")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def _call_ai(messages: list, max_tokens: int = 500) -> str:
    """Асинхронный вызов OpenRouter API с ретраем при ошибках"""
    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY not set, AI features disabled")
        return ""

    last_error = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": OPENROUTER_MODEL,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": 0.3,
                    },
                )
                response.raise_for_status()
                result = response.json()
                content = result["choices"][0]["message"]["content"]
                return content.strip() if content else ""
        except Exception as e:
            last_error = e
            logger.warning(f"AI API error (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)  # 1s, 2s экспоненциальная задержка

    logger.error(f"AI API failed after 3 attempts: {last_error}")
    return ""


async def generate_summary(title: str, description: str = "") -> str:
    """Генерировать краткое описание новости, если его нет"""
    if not OPENROUTER_API_KEY:
        return description
    if description and len(description) > 50:
        return description

    messages = [
        {
            "role": "system",
            "content": (
                "Ты — редактор новостного бота. "
                "Напиши короткое описание новости (2-3 предложения) на основе заголовка. "
                "Пиши на русском языке, нейтральным тоном, по делу."
            ),
        },
        {
            "role": "user",
            "content": f"Заголовок: {title}",
        },
    ]

    result = await _call_ai(messages, max_tokens=1000)
    return result if result else description
