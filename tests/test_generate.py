"""Tests for its_briefing.generate."""
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from its_briefing.config import Category, Settings, Source
from its_briefing.generate import run
from its_briefing.models import Article, Briefing, Bullet, ExecutiveSummary


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


def _sample_article(idx: int) -> Article:
    return Article(
        id=f"id{idx}",
        source="Test",
        source_lang="EN",
        title=f"Title {idx}",
        link=f"https://example.com/{idx}",
        published=datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc),
        summary="summary",
    )


def _fake_summary() -> ExecutiveSummary:
    return ExecutiveSummary(
        critical_vulnerabilities=[Bullet(text="bullet", article_ids=["id1"])]
    )


@freeze_time("2026-04-07 06:00:00", tz_offset=0)
def test_run_orchestrates_pipeline(tmp_path: Path) -> None:
    sources = [Source(name="Test", url="https://example.com/feed", lang="EN")]
    categories = [Category(name="IT-Security", description="defense")]
    fake_articles = [_sample_article(1), _sample_article(2)]

    with (
        patch("its_briefing.generate.config.load_sources", return_value=sources),
        patch("its_briefing.generate.config.load_categories", return_value=categories),
        patch("its_briefing.generate.config.Settings.from_env", return_value=_settings()),
        patch("its_briefing.generate.fetch.fetch_all", return_value=(fake_articles, ["BadFeed"])),
        patch("its_briefing.generate.llm.classify_article", side_effect=["IT-Security", "IT-Security"]),
        patch("its_briefing.generate.llm.build_summary", return_value=_fake_summary()),
    ):
        briefing = run(cache_dir=tmp_path)

    assert isinstance(briefing, Briefing)
    assert briefing.date == date(2026, 4, 7)
    assert briefing.article_count == 2
    assert briefing.failed_sources == ["BadFeed"]
    assert briefing.articles[0].category == "IT-Security"
    assert (tmp_path / "briefing-2026-04-07.json").exists()


@freeze_time("2026-04-07 06:00:00", tz_offset=0)
def test_run_returns_none_on_unhandled_exception(tmp_path: Path) -> None:
    with patch(
        "its_briefing.generate.config.load_sources", side_effect=RuntimeError("boom")
    ):
        result = run(cache_dir=tmp_path)

    assert result is None
