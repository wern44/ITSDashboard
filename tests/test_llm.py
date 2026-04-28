"""Tests for its_briefing.llm — both Ollama and LM Studio clients."""
import json
from datetime import date, datetime, timezone

import pytest
from pytest_httpx import HTTPXMock

from its_briefing.config import Category, Settings
from its_briefing.llm import (
    LLMClientError,
    LMStudioClient,
    OllamaClient,
    build_summary,
    classify_article,
    make_client,
)
from its_briefing.models import Article, ExecutiveSummary


def _article() -> Article:
    return Article(
        id="abc123",
        source="Test",
        source_lang="EN",
        title="Critical zero-day in Foo software",
        link="https://example.com/x",
        published=datetime(2026, 4, 7, 10, 0, tzinfo=timezone.utc),
        summary="A new 0day was disclosed.",
    )


def _categories() -> list[Category]:
    return [
        Category(name="0-Day", description="Zero-days"),
        Category(name="Hacks", description="Breaches"),
        Category(name="Regulation", description="Compliance"),
    ]


def _settings(provider: str, base_url: str) -> Settings:
    return Settings(
        llm_provider=provider,
        llm_base_url=base_url,
        llm_model="test-model",
        timezone="Europe/Berlin",
        schedule_hour=6,
        schedule_minute=0,
        flask_host="127.0.0.1",
        flask_port=8089,
        log_level="INFO",
    )


# ---------- provider matrix used by parameterized tests ----------

OLLAMA = ("ollama", "http://localhost:11434", "/api/chat")
LMSTUDIO = ("lmstudio", "http://localhost:1234", "/v1/chat/completions")


def _success_response(provider: str, content: str) -> dict:
    if provider == "ollama":
        return {"message": {"content": content}}
    return {"choices": [{"message": {"content": content}}]}


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_classify_article_returns_chosen_category(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(
        url=f"{base_url}{path}",
        json=_success_response(provider, '{"category": "0-Day"}'),
    )
    result = classify_article(_article(), _categories(), _settings(provider, base_url))
    assert result == "0-Day"


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_classify_article_unknown_category_falls_back(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(
        url=f"{base_url}{path}",
        json=_success_response(provider, '{"category": "Bogus"}'),
    )
    assert classify_article(_article(), _categories(), _settings(provider, base_url)) == "Uncategorized"


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_classify_article_invalid_json_falls_back(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(
        url=f"{base_url}{path}",
        json=_success_response(provider, "this is not json"),
    )
    assert classify_article(_article(), _categories(), _settings(provider, base_url)) == "Uncategorized"


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_classify_article_http_error_falls_back(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(url=f"{base_url}{path}", status_code=500)
    assert classify_article(_article(), _categories(), _settings(provider, base_url)) == "Uncategorized"


def test_classify_article_lmstudio_missing_choices_key_falls_back(
    httpx_mock: HTTPXMock,
) -> None:
    """Locks the bug fix: an LM Studio response without 'choices' must not raise."""
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"unexpected": "shape"},
    )
    assert (
        classify_article(_article(), _categories(), _settings("lmstudio", "http://localhost:1234"))
        == "Uncategorized"
    )


# ---------- summary tests ----------

def _articles() -> list[Article]:
    return [
        Article(
            id="id1",
            source="Test",
            source_lang="EN",
            title="CVE-2026-0001 critical RCE in WidgetServer",
            link="https://example.com/1",
            published=datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc),
            summary="A critical RCE was disclosed.",
            category="0-Day",
        ),
        Article(
            id="id2",
            source="Test",
            source_lang="EN",
            title="Ransomware hits hospital chain",
            link="https://example.com/2",
            published=datetime(2026, 4, 7, 8, 0, tzinfo=timezone.utc),
            summary="A ransomware group attacked.",
            category="Hacks",
        ),
    ]


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_build_summary_parses_structured_response(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    structured = {
        "critical_vulnerabilities": [
            {"text": "CVE-2026-0001 RCE in WidgetServer", "article_ids": ["id1"]}
        ],
        "active_threats": [],
        "notable_incidents": [
            {"text": "Hospital chain hit by ransomware", "article_ids": ["id2"]}
        ],
        "strategic_policy": [],
    }
    httpx_mock.add_response(
        url=f"{base_url}{path}",
        json=_success_response(provider, json.dumps(structured)),
    )
    s = build_summary(_articles(), _settings(provider, base_url), target_date=date(2026, 4, 7))
    assert isinstance(s, ExecutiveSummary)
    assert s.critical_vulnerabilities[0].text.startswith("CVE-2026-0001")


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_build_summary_invalid_json_falls_back(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(url=f"{base_url}{path}", json=_success_response(provider, "garbage"))
    httpx_mock.add_response(url=f"{base_url}{path}", json=_success_response(provider, "garbage"))
    s = build_summary(_articles(), _settings(provider, base_url), target_date=date(2026, 4, 7))
    assert s.critical_vulnerabilities[0].text.startswith("AI summary unavailable")


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_build_summary_http_error_falls_back(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(url=f"{base_url}{path}", status_code=500)
    httpx_mock.add_response(url=f"{base_url}{path}", status_code=500)
    s = build_summary(_articles(), _settings(provider, base_url), target_date=date(2026, 4, 7))
    assert s.critical_vulnerabilities[0].text.startswith("AI summary unavailable")


# ---------- list_models tests ----------

def test_ollama_list_models(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/tags",
        json={"models": [{"name": "llama3.1:8b"}, {"name": "mistral:7b"}]},
    )
    client = OllamaClient("http://localhost:11434", "llama3.1:8b")
    assert client.list_models() == ["llama3.1:8b", "mistral:7b"]


def test_lmstudio_list_models(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/models",
        json={"data": [{"id": "google/gemma-4-26b-a4b"}, {"id": "qwen/qwen2.5"}]},
    )
    client = LMStudioClient("http://localhost:1234", "google/gemma-4-26b-a4b")
    assert client.list_models() == ["google/gemma-4-26b-a4b", "qwen/qwen2.5"]


def test_make_client_dispatches_on_provider() -> None:
    s_ollama = _settings("ollama", "http://localhost:11434")
    s_lm = _settings("lmstudio", "http://localhost:1234")
    assert isinstance(make_client(s_ollama), OllamaClient)
    assert isinstance(make_client(s_lm), LMStudioClient)


# ---------- code-fence stripping (Gemma via LM Studio wraps JSON in ```json ... ```) ----------

def test_classify_article_strips_markdown_code_fences(httpx_mock: HTTPXMock) -> None:
    """Gemma wraps JSON in ```json ... ``` even when asked for raw JSON."""
    fenced = '```json\n{"category": "0-Day"}\n```'
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": fenced}}]},
    )
    result = classify_article(
        _article(), _categories(), _settings("lmstudio", "http://localhost:1234")
    )
    assert result == "0-Day"


def test_build_summary_strips_markdown_code_fences(httpx_mock: HTTPXMock) -> None:
    structured = {
        "critical_vulnerabilities": [{"text": "x", "article_ids": ["id1"]}],
        "active_threats": [],
        "notable_incidents": [],
        "strategic_policy": [],
    }
    fenced = "```json\n" + json.dumps(structured) + "\n```"
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": fenced}}]},
    )
    s = build_summary(
        _articles(),
        _settings("lmstudio", "http://localhost:1234"),
        target_date=date(2026, 4, 7),
    )
    assert s.critical_vulnerabilities[0].text == "x"
