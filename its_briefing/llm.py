"""Local Ollama client: classify articles and build executive summary."""
from __future__ import annotations

import json
import logging
from datetime import date

import httpx
from pydantic import ValidationError

from its_briefing.config import Category, Settings
from its_briefing.models import Article, ExecutiveSummary

logger = logging.getLogger(__name__)

OLLAMA_TIMEOUT_SECONDS = 60
UNCATEGORIZED = "Uncategorized"


def _ollama_chat(prompt: str, settings: Settings) -> str:
    """Call Ollama /api/chat with format=json. Returns the assistant content string."""
    payload = {
        "model": settings.ollama_model,
        "format": "json",
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }
    response = httpx.post(
        f"{settings.ollama_base_url}/api/chat",
        json=payload,
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    return data["message"]["content"]


def _classification_prompt(article: Article, categories: list[Category]) -> str:
    cat_lines = "\n".join(f"- {c.name}: {c.description}" for c in categories)
    return (
        "You are a cybersecurity news classifier. Pick exactly ONE category for the article.\n\n"
        f"Categories:\n{cat_lines}\n\n"
        f"Article title: {article.title}\n"
        f"Article summary: {article.summary[:500]}\n\n"
        'Respond with JSON only: {"category": "<one of the names above>"}'
    )


def classify_article(article: Article, categories: list[Category], settings: Settings) -> str:
    """Classify a single article into one of the configured categories."""
    valid_names = {c.name for c in categories}
    try:
        content = _ollama_chat(_classification_prompt(article, categories), settings)
        parsed = json.loads(content)
        chosen = parsed.get("category", "")
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Classification failed for %s: %s", article.id, exc)
        return UNCATEGORIZED

    if chosen not in valid_names:
        logger.warning("Classifier returned unknown category %r for %s", chosen, article.id)
        return UNCATEGORIZED
    return chosen
