"""Tests for its_briefing.db."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from its_briefing.config import Settings
from its_briefing.db import (
    get_connection,
    get_settings,
    init_schema,
    latest_briefing as db_latest_briefing,
    save_briefing as db_save_briefing,
    seed_settings_from_env,
    update_settings,
    upsert_article,
)
from its_briefing.models import Article, Briefing, Bullet, ExecutiveSummary


def test_get_connection_creates_db_file(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    assert db_path.exists()
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_get_connection_enables_foreign_keys(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "test.db")
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1
    conn.close()


def test_init_schema_creates_all_tables(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert tables >= {
        "settings",
        "articles",
        "briefings",
        "briefing_articles",
        "generation_runs",
        "schema_version",
    }
    conn.close()


def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    init_schema(conn)  # should not raise
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 1
    conn.close()


def _env_settings() -> Settings:
    return Settings(
        llm_provider="ollama",
        llm_base_url="http://localhost:11434",
        llm_model="llama3.1:8b",
        timezone="Europe/Berlin",
        schedule_hour=6,
        schedule_minute=0,
        flask_host="127.0.0.1",
        flask_port=8089,
        log_level="INFO",
    )


def test_seed_settings_writes_when_table_empty(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    seed_settings_from_env(conn, _env_settings())
    s = get_settings(conn)
    assert s.llm_provider == "ollama"
    assert s.llm_model == "llama3.1:8b"
    assert s.schedule_hour == 6
    conn.close()


def test_seed_settings_is_noop_when_table_populated(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    seed_settings_from_env(conn, _env_settings())
    update_settings(conn, {"llm_provider": "lmstudio", "llm_model": "gemma"})

    # Re-seed should be a no-op.
    seed_settings_from_env(conn, _env_settings())
    s = get_settings(conn)
    assert s.llm_provider == "lmstudio"
    assert s.llm_model == "gemma"
    conn.close()


def test_update_settings_partial(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    seed_settings_from_env(conn, _env_settings())
    update_settings(conn, {"llm_base_url": "http://newhost:1234"})
    s = get_settings(conn)
    assert s.llm_base_url == "http://newhost:1234"
    assert s.llm_provider == "ollama"  # unchanged
    conn.close()


def test_update_settings_rejects_unknown_key(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    seed_settings_from_env(conn, _env_settings())
    with pytest.raises(KeyError):
        update_settings(conn, {"bogus_key": "x"})
    conn.close()


def _make_article(id_: str = "abc12345", link: str = "https://x.example/1") -> Article:
    return Article(
        id=id_,
        source="Test",
        source_lang="EN",
        title="A title",
        link=link,
        published=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
        summary="summary text",
        category="0-Day",
    )


def _make_briefing(d: date, articles: list[Article]) -> Briefing:
    return Briefing(
        date=d,
        generated_at=datetime(d.year, d.month, d.day, 6, 0, tzinfo=timezone.utc),
        summary=ExecutiveSummary(
            critical_vulnerabilities=[Bullet(text="x", article_ids=[a.id for a in articles])]
        ),
        articles=articles,
        failed_sources=["BadFeed"],
        article_count=len(articles),
    )


def test_upsert_article_inserts_then_updates(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    a = _make_article()
    upsert_article(conn, a, first_seen=datetime(2026, 4, 28, 5, 0, tzinfo=timezone.utc))
    a2 = _make_article()
    a2.category = "Hacks"
    upsert_article(conn, a2, first_seen=datetime(2026, 4, 29, 5, 0, tzinfo=timezone.utc))

    rows = conn.execute("SELECT id, category, first_seen FROM articles").fetchall()
    assert len(rows) == 1
    assert rows[0]["category"] == "Hacks"
    # first_seen is preserved from the original insert
    assert rows[0]["first_seen"].startswith("2026-04-28")
    conn.close()


def test_save_briefing_round_trip(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    a = _make_article()
    b = _make_briefing(date(2026, 4, 28), [a])
    db_save_briefing(conn, b)

    loaded = db_latest_briefing(conn)
    assert loaded is not None
    assert loaded.date == date(2026, 4, 28)
    assert loaded.article_count == 1
    assert loaded.articles[0].id == "abc12345"
    assert loaded.failed_sources == ["BadFeed"]
    assert loaded.summary.critical_vulnerabilities[0].text == "x"
    conn.close()


def test_latest_briefing_returns_newest(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    a = _make_article()
    db_save_briefing(conn, _make_briefing(date(2026, 4, 27), [a]))
    db_save_briefing(conn, _make_briefing(date(2026, 4, 28), [a]))
    loaded = db_latest_briefing(conn)
    assert loaded.date == date(2026, 4, 28)
    conn.close()


def test_latest_briefing_returns_none_when_empty(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    assert db_latest_briefing(conn) is None
    conn.close()
