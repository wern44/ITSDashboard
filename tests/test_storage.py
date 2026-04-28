"""Tests for its_briefing.storage (DB-backed)."""
from datetime import date, datetime, timezone
from pathlib import Path

from its_briefing import storage
from its_briefing.db import get_connection, init_schema
from its_briefing.models import Article, Briefing, Bullet, ExecutiveSummary


def _briefing(d: date) -> Briefing:
    a = Article(
        id="abc12345",
        source="Test",
        source_lang="EN",
        title="x",
        link="https://example.com/x",
        published=datetime(d.year, d.month, d.day, 9, 0, tzinfo=timezone.utc),
        summary="x",
        category="0-Day",
    )
    return Briefing(
        date=d,
        generated_at=datetime(d.year, d.month, d.day, 6, 0, tzinfo=timezone.utc),
        summary=ExecutiveSummary(critical_vulnerabilities=[Bullet(text="x", article_ids=["abc12345"])]),
        articles=[a],
        failed_sources=[],
        article_count=1,
    )


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    b = _briefing(date(2026, 4, 28))
    storage.save_briefing(b, db_path=db_path)
    loaded = storage.load_briefing(date(2026, 4, 28), db_path=db_path)
    assert loaded is not None
    assert loaded.date == date(2026, 4, 28)
    assert loaded.articles[0].id == "abc12345"


def test_load_briefing_missing_returns_none(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    assert storage.load_briefing(date(2026, 4, 1), db_path=db_path) is None


def test_latest_briefing_returns_newest(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    storage.save_briefing(_briefing(date(2026, 4, 27)), db_path=db_path)
    storage.save_briefing(_briefing(date(2026, 4, 28)), db_path=db_path)
    loaded = storage.latest_briefing(db_path=db_path)
    assert loaded.date == date(2026, 4, 28)


def test_latest_briefing_returns_none_when_no_briefings(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    assert storage.latest_briefing(db_path=db_path) is None
