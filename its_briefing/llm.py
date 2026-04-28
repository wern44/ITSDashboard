"""LLM clients (Ollama + LM Studio) and the classify/summarize entry points."""
from __future__ import annotations

import json
import logging
from datetime import date

import httpx
from pydantic import ValidationError

from its_briefing.config import Category, Settings
from its_briefing.models import Article, ExecutiveSummary

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 60
UNCATEGORIZED = "Uncategorized"


class LLMClientError(Exception):
    """Raised by an LLM client when a chat call fails for any reason."""


class OllamaClient:
    """Client for Ollama's native /api/chat endpoint."""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(self, prompt: str) -> str:
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "format": "json",
                    "stream": False,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=LLM_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            return data["message"]["content"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise LLMClientError(str(exc)) from exc

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            return [m["name"] for m in response.json().get("models", [])]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise LLMClientError(str(exc)) from exc


class LMStudioClient:
    """Client for LM Studio's OpenAI-compatible /v1/chat/completions endpoint."""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(self, prompt: str) -> str:
        try:
            response = httpx.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": self.model,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=LLM_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError) as exc:
            raise LLMClientError(str(exc)) from exc

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.base_url}/v1/models", timeout=5)
            response.raise_for_status()
            return [m["id"] for m in response.json().get("data", [])]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise LLMClientError(str(exc)) from exc


LLMClient = OllamaClient | LMStudioClient


def make_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "ollama":
        return OllamaClient(settings.llm_base_url, settings.llm_model)
    return LMStudioClient(settings.llm_base_url, settings.llm_model)


def _classification_prompt(article: Article, categories: list[Category]) -> str:
    cat_lines = "\n".join(f"- {c.name}: {c.description}" for c in categories)
    return (
        "You are a cybersecurity news classifier. Pick exactly ONE category for the article.\n\n"
        f"Categories:\n{cat_lines}\n\n"
        f"Article title: {article.title}\n"
        f"Article summary: {article.summary[:500]}\n\n"
        'Respond with JSON only: {"category": "<one of the names above>"}'
    )


def classify_article(
    article: Article, categories: list[Category], settings: Settings
) -> str:
    """Classify a single article into one of the configured categories."""
    valid_names = {c.name for c in categories}
    client = make_client(settings)
    try:
        content = client.chat(_classification_prompt(article, categories))
        parsed = json.loads(content)
        chosen = parsed.get("category", "")
    except (LLMClientError, json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Classification failed for %s: %s", article.id, exc)
        return UNCATEGORIZED

    if chosen not in valid_names:
        logger.warning("Classifier returned unknown category %r for %s", chosen, article.id)
        return UNCATEGORIZED
    return chosen


def _summary_prompt(articles: list[Article]) -> str:
    article_lines = []
    for a in articles:
        cat = a.category or "Uncategorized"
        snippet = a.summary[:300].replace("\n", " ")
        article_lines.append(f"[{a.id}] ({cat}) {a.title} — {snippet}")
    article_block = "\n".join(article_lines)
    return (
        "You are a cybersecurity briefing analyst. Read the articles below and produce an "
        "executive summary in four sections.\n\n"
        "Each section is a list of bullets. Each bullet has a short text (1-2 sentences) and a "
        "list of article_ids that support it. Use the bracketed [id] from each article line.\n\n"
        "Sections:\n"
        "- critical_vulnerabilities: CVEs, advisories, urgent patches\n"
        "- active_threats: ongoing campaigns, malware, threat actor activity\n"
        "- notable_incidents: confirmed breaches, ransomware victims, leaks\n"
        "- strategic_policy: regulation, geopolitics, industry trends\n\n"
        "Empty sections are allowed (return an empty list). Be concise.\n\n"
        f"Articles:\n{article_block}\n\n"
        'Respond with JSON only, matching this exact shape:\n'
        '{"critical_vulnerabilities":[{"text":"...","article_ids":["..."]}],'
        '"active_threats":[],"notable_incidents":[],"strategic_policy":[]}'
    )


def _try_build_summary(articles: list[Article], settings: Settings) -> ExecutiveSummary:
    client = make_client(settings)
    content = client.chat(_summary_prompt(articles))
    parsed = json.loads(content)
    return ExecutiveSummary.model_validate(parsed)


def build_summary(
    articles: list[Article], settings: Settings, target_date: date
) -> ExecutiveSummary:
    """Build the executive summary, with one retry and a placeholder fallback."""
    for attempt in (1, 2):
        try:
            return _try_build_summary(articles, settings)
        except (LLMClientError, json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            logger.warning("Summary attempt %d failed: %s", attempt, exc)
    return ExecutiveSummary.placeholder(target_date)
