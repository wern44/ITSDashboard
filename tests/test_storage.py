"""Tests for its_briefing.storage."""
from datetime import date, datetime, timezone
from pathlib import Path

from its_briefing.models import Article, Briefing, Bullet, ExecutiveSummary
from its_briefing.storage import latest_briefing, load_briefing, save_briefing


def _make_briefing(d: date, link: str = "https://example.com/a") -> Briefing:
    article = Article(
        id=Article.make_id(link),
        source="Test Feed",
        source_lang="EN",
        title="Title",
        link=link,
        published=datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc),
        summary="summary",
        category="IT-Security",
    )
    return Briefing(
        date=d,
        generated_at=datetime(2026, 4, 7, 6, 0, 0, tzinfo=timezone.utc),
        summary=ExecutiveSummary(
            critical_vulnerabilities=[Bullet(text="bullet", article_ids=[article.id])]
        ),
        articles=[article],
        failed_sources=[],
        article_count=1,
    )


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    briefing = _make_briefing(date(2026, 4, 7))
    save_briefing(briefing, cache_dir=tmp_path)

    loaded = load_briefing(date(2026, 4, 7), cache_dir=tmp_path)

    assert loaded is not None
    assert loaded.date == date(2026, 4, 7)
    assert loaded.article_count == 1
    assert loaded.articles[0].title == "Title"


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_briefing(date(2026, 4, 7), cache_dir=tmp_path) is None


def test_latest_briefing_picks_highest_date(tmp_path: Path) -> None:
    save_briefing(_make_briefing(date(2026, 4, 5)), cache_dir=tmp_path)
    save_briefing(_make_briefing(date(2026, 4, 7)), cache_dir=tmp_path)
    save_briefing(_make_briefing(date(2026, 4, 6)), cache_dir=tmp_path)

    latest = latest_briefing(cache_dir=tmp_path)

    assert latest is not None
    assert latest.date == date(2026, 4, 7)


def test_latest_briefing_empty_dir(tmp_path: Path) -> None:
    assert latest_briefing(cache_dir=tmp_path) is None


def test_save_creates_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "cache"
    save_briefing(_make_briefing(date(2026, 4, 7)), cache_dir=target)

    assert (target / "briefing-2026-04-07.json").exists()
