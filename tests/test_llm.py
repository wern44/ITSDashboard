"""Tests for its_briefing.llm."""
from datetime import datetime, timezone

from pytest_httpx import HTTPXMock

from its_briefing.config import Category, Settings
from its_briefing.llm import classify_article
from its_briefing.models import Article


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


def _settings() -> Settings:
    return Settings(
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.1:8b",
        timezone="Europe/Berlin",
        schedule_hour=6,
        schedule_minute=0,
        flask_host="127.0.0.1",
        flask_port=8089,
        log_level="INFO",
    )


def test_classify_article_returns_chosen_category(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": '{"category": "0-Day"}'}},
    )

    result = classify_article(_article(), _categories(), _settings())

    assert result == "0-Day"


def test_classify_article_unknown_category_falls_back(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": '{"category": "Bogus"}'}},
    )

    result = classify_article(_article(), _categories(), _settings())

    assert result == "Uncategorized"


def test_classify_article_invalid_json_falls_back(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": "this is not json"}},
    )

    result = classify_article(_article(), _categories(), _settings())

    assert result == "Uncategorized"


def test_classify_article_http_error_falls_back(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://localhost:11434/api/chat", status_code=500)

    result = classify_article(_article(), _categories(), _settings())

    assert result == "Uncategorized"
