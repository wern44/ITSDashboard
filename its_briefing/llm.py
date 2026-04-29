"""LLM clients (Ollama + LM Studio) and the classify/summarize entry points."""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

import httpx
from pydantic import ValidationError

from its_briefing.config import Category, Settings
from its_briefing.models import Article, Bullet, ExecutiveSummary

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 60
UNCATEGORIZED = "Uncategorized"


class LLMClientError(Exception):
    """Raised by an LLM client when a chat call fails for any reason."""


def _raise_for_status_with_body(response: httpx.Response) -> None:
    """Like response.raise_for_status() but includes the response body in the message.

    Servers like LM Studio return useful diagnostics in the 400 body
    (e.g. context-length-exceeded). The default httpx error message hides them.
    """
    if response.is_success:
        return
    body = response.text[:500] if response.text else ""
    raise LLMClientError(
        f"HTTP {response.status_code} from {response.request.url}: {body}"
    )


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
            _raise_for_status_with_body(response)
            data = response.json()
            return data["message"]["content"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise LLMClientError(str(exc)) from exc

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            _raise_for_status_with_body(response)
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
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=LLM_TIMEOUT_SECONDS,
            )
            _raise_for_status_with_body(response)
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError) as exc:
            raise LLMClientError(str(exc)) from exc

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.base_url}/v1/models", timeout=5)
            _raise_for_status_with_body(response)
            return [m["id"] for m in response.json().get("data", [])]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise LLMClientError(str(exc)) from exc


LLMClient = OllamaClient | LMStudioClient


def make_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "ollama":
        return OllamaClient(settings.llm_base_url, settings.llm_model)
    return LMStudioClient(settings.llm_base_url, settings.llm_model)


def _strip_code_fences(text: str) -> str:
    """Strip Markdown code fences from an LLM response.

    Some models (notably Gemma via LM Studio) wrap structured output in
    ```json ... ``` or ``` ... ``` even when the prompt asks for raw JSON.
    """
    s = text.strip()
    if s.startswith("```"):
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


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
        parsed = json.loads(_strip_code_fences(content))
        chosen = parsed.get("category", "")
    except (LLMClientError, json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Classification failed for %s: %s", article.id, exc)
        return UNCATEGORIZED

    if chosen not in valid_names:
        logger.warning("Classifier returned unknown category %r for %s", chosen, article.id)
        return UNCATEGORIZED
    return chosen


SECTION_CATEGORIES: dict[str, list[str]] = {
    "critical_vulnerabilities": ["Threats and Vulnerabilities", "0-Day"],
    "active_threats":           ["0-Day", "Hacks", "Phishing"],
    "notable_incidents":        ["Hacks"],
    "strategic_policy":         ["Regulation", "Cyber-Security", "IT-Security"],
}

_SECTION_DESCRIPTIONS: dict[str, str] = {
    "critical_vulnerabilities": "CVEs, advisories, urgent patches",
    "active_threats":           "ongoing campaigns, malware, phishing, threat actor activity",
    "notable_incidents":        "confirmed breaches, ransomware victims, data leaks",
    "strategic_policy":         "regulation, geopolitics, industry trends",
}


def _section_prompt(section: str, articles: list[Article]) -> str:
    article_lines = []
    for a in articles:
        snippet = a.summary[:300].replace("\n", " ")
        article_lines.append(f"[{a.id}] {a.title} — {snippet}")
    article_block = "\n".join(article_lines)
    description = _SECTION_DESCRIPTIONS[section]
    return (
        "You are a cybersecurity briefing analyst. Read the articles below and produce "
        f"a list of bullets for the section: {section} ({description}).\n\n"
        "Each bullet has a short text (1-2 sentences) and a list of article_ids that "
        "support it. Use the bracketed [id] from each article line. An empty list is "
        "allowed.\n\n"
        f"Articles:\n{article_block}\n\n"
        'Respond with JSON only: {"bullets":[{"text":"...","article_ids":["..."]}]}'
    )


def _try_build_section(
    section: str, articles: list[Article], settings: Settings
) -> list[Bullet]:
    client = make_client(settings)
    content = client.chat(_section_prompt(section, articles))
    parsed = json.loads(_strip_code_fences(content))
    bullets_raw = parsed.get("bullets", [])
    return [Bullet.model_validate(b) for b in bullets_raw]


def build_summary(
    articles: list[Article], settings: Settings, target_date: date
) -> tuple[ExecutiveSummary, Optional[str]]:
    """Per-section summarization. Returns (summary, last_error_message_or_None)."""
    section_results: dict[str, list[Bullet]] = {k: [] for k in SECTION_CATEGORIES}
    last_error: Optional[str] = None
    section_failed: dict[str, bool] = {k: False for k in SECTION_CATEGORIES}

    for section, allowed in SECTION_CATEGORIES.items():
        subset = [a for a in articles if a.category in allowed]
        if not subset:
            continue
        success = False
        for attempt in (1, 2):
            try:
                section_results[section] = _try_build_section(section, subset, settings)
                success = True
                break
            except (LLMClientError, json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
                logger.warning("Section %s attempt %d failed: %s", section, attempt, exc)
                last_error = str(exc)
        if not success:
            section_failed[section] = True

    # If every populated section failed, return the placeholder.
    populated = [s for s, allowed in SECTION_CATEGORIES.items()
                 if any(a.category in allowed for a in articles)]
    if populated and all(section_failed[s] for s in populated):
        return ExecutiveSummary.placeholder(target_date), last_error

    return (
        ExecutiveSummary(
            critical_vulnerabilities=section_results["critical_vulnerabilities"],
            active_threats=section_results["active_threats"],
            notable_incidents=section_results["notable_incidents"],
            strategic_policy=section_results["strategic_policy"],
        ),
        last_error,
    )
