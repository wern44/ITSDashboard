"""Tests for its_briefing.fetch."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from freezegun import freeze_time
from pytest_httpx import HTTPXMock

from its_briefing.config import Source
from its_briefing.fetch import fetch_all, parse_feed_bytes

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


@freeze_time("2026-04-07 12:00:00", tz_offset=0)
def test_parse_feed_returns_only_recent_articles() -> None:
    source = Source(name="Test Feed", url="https://example.com/feed", lang="EN")
    raw = FIXTURE.read_bytes()

    articles = parse_feed_bytes(raw, source, now=datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc))

    assert len(articles) == 1
    assert articles[0].title == "Recent article"
    assert articles[0].source == "Test Feed"
    assert articles[0].source_lang == "EN"
    assert articles[0].link == "https://example.com/recent"
    assert articles[0].id == articles[0].make_id("https://example.com/recent")


def test_parse_malformed_feed_returns_empty() -> None:
    source = Source(name="Bad Feed", url="https://example.com/bad", lang="EN")

    articles = parse_feed_bytes(b"<not-xml>", source, now=datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc))

    assert articles == []


@freeze_time("2026-04-07 12:00:00", tz_offset=0)
def test_fetch_all_aggregates_articles(httpx_mock: HTTPXMock) -> None:
    raw = FIXTURE.read_bytes()
    httpx_mock.add_response(url="https://a.example/feed", content=raw)
    httpx_mock.add_response(url="https://b.example/feed", content=raw)

    sources = [
        Source(name="A", url="https://a.example/feed", lang="EN"),
        Source(name="B", url="https://b.example/feed", lang="DE"),
    ]
    articles, failed = fetch_all(sources)

    assert failed == []
    assert len(articles) == 2  # one recent article per feed
    assert {a.source for a in articles} == {"A", "B"}


@freeze_time("2026-04-07 12:00:00", tz_offset=0)
def test_fetch_all_records_failures(httpx_mock: HTTPXMock) -> None:
    raw = FIXTURE.read_bytes()
    httpx_mock.add_response(url="https://ok.example/feed", content=raw)
    httpx_mock.add_response(url="https://broken.example/feed", status_code=500)

    sources = [
        Source(name="OK", url="https://ok.example/feed", lang="EN"),
        Source(name="Broken", url="https://broken.example/feed", lang="EN"),
    ]
    articles, failed = fetch_all(sources)

    assert "Broken" in failed
    assert any(a.source == "OK" for a in articles)
