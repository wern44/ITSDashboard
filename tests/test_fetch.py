"""Tests for its_briefing.fetch."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from freezegun import freeze_time

from its_briefing.config import Source
from its_briefing.fetch import parse_feed_bytes

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
