"""Tests for its_briefing.generate."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from its_briefing import generate
from its_briefing.config import Settings
from its_briefing.db import create_source, get_connection, init_schema, list_sources, seed_settings_from_env
from its_briefing.models import Article, ExecutiveSummary


def _seed_db(db_path: Path) -> None:
    conn = get_connection(db_path)
    init_schema(conn)
    seed_settings_from_env(
        conn,
        Settings(
            llm_provider="ollama",
            llm_base_url="http://localhost:11434",
            llm_model="llama3.1:8b",
            timezone="Europe/Berlin",
            schedule_hour=6,
            schedule_minute=0,
            flask_host="127.0.0.1",
            flask_port=8089,
            log_level="INFO",
        ),
    )
    conn.close()


def _patch_db_paths(monkeypatch, db_path: Path) -> None:
    monkeypatch.setattr("its_briefing.db.DEFAULT_DB_PATH", db_path)


def _make_article(id_="a1") -> Article:
    return Article(
        id=id_,
        source="Test",
        source_lang="EN",
        title=f"Article {id_}",
        link=f"https://example.com/{id_}",
        published=datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc),
        summary="text",
    )


def test_run_orchestrates_pipeline(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    _seed_db(db_path)
    _patch_db_paths(monkeypatch, db_path)

    article = _make_article()
    monkeypatch.setattr(generate.fetch, "fetch_all", lambda sources: ([article], []))
    monkeypatch.setattr(generate.llm, "classify_article", lambda *a, **k: "0-Day")
    monkeypatch.setattr(
        generate.llm, "build_summary", lambda articles, settings, target_date: (ExecutiveSummary(), None)
    )

    briefing = generate.run()
    assert briefing is not None
    assert briefing.article_count == 1
    assert briefing.articles[0].category == "0-Day"


def test_run_returns_none_on_failure(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    _seed_db(db_path)
    _patch_db_paths(monkeypatch, db_path)

    def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(generate.fetch, "fetch_all", boom)

    result = generate.run()
    assert result is None


def test_run_records_generation_run_on_success(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    _seed_db(db_path)
    _patch_db_paths(monkeypatch, db_path)

    monkeypatch.setattr(generate.fetch, "fetch_all", lambda sources: ([], []))
    monkeypatch.setattr(generate.llm, "classify_article", lambda *a, **k: "Uncategorized")
    monkeypatch.setattr(
        generate.llm, "build_summary", lambda articles, settings, target_date: (ExecutiveSummary(), None)
    )

    result = generate.run()
    assert result is not None

    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT succeeded, error FROM generation_runs ORDER BY id"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["succeeded"] == 1
    assert rows[0]["error"] is None


def test_run_records_failed_run_on_exception(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    _seed_db(db_path)
    _patch_db_paths(monkeypatch, db_path)

    def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(generate.fetch, "fetch_all", boom)

    result = generate.run()
    assert result is None

    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT succeeded, error FROM generation_runs ORDER BY id"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["succeeded"] == 0
    assert "synthetic failure" in rows[0]["error"]


def test_run_returns_none_when_db_setup_fails(tmp_path: Path, monkeypatch) -> None:
    """If the DB setup itself fails, run() must still return None — never propagate."""
    db_path = tmp_path / "test.db"
    _seed_db(db_path)
    _patch_db_paths(monkeypatch, db_path)

    # Force config.Settings.from_env to blow up during setup
    def boom_from_env():
        raise RuntimeError("setup synthetic failure")

    monkeypatch.setattr("its_briefing.config.Settings.from_env", boom_from_env)

    result = generate.run()
    assert result is None


def test_run_uses_only_enabled_sources(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    _seed_db(db_path)
    _patch_db_paths(monkeypatch, db_path)

    conn = get_connection(db_path)
    create_source(conn, name="Enabled", url="https://a/", lang="EN", enabled=True)
    create_source(conn, name="Disabled", url="https://b/", lang="EN", enabled=False)
    conn.close()

    captured: list = []
    def fake_fetch_all(sources):
        captured.extend(sources)
        return ([], [])
    monkeypatch.setattr(generate.fetch, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(generate.llm, "classify_article", lambda *a, **k: "Uncategorized")
    monkeypatch.setattr(
        generate.llm, "build_summary",
        lambda articles, settings, target_date: (ExecutiveSummary(), None),
    )

    generate.run()
    names = {s.name for s in captured}
    assert names == {"Enabled"}


def test_run_back_feeds_source_statuses(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    _seed_db(db_path)
    _patch_db_paths(monkeypatch, db_path)

    conn = get_connection(db_path)
    sid_ok = create_source(conn, name="GoodFeed", url="https://a/", lang="EN", enabled=True)
    sid_bad = create_source(conn, name="BadFeed", url="https://b/", lang="EN", enabled=True)
    conn.close()

    monkeypatch.setattr(
        generate.fetch, "fetch_all", lambda sources: ([_make_article("a1")], ["BadFeed"])
    )
    monkeypatch.setattr(generate.llm, "classify_article", lambda *a, **k: "Uncategorized")
    monkeypatch.setattr(
        generate.llm, "build_summary",
        lambda articles, settings, target_date: (ExecutiveSummary(), None),
    )

    generate.run()

    conn = get_connection(db_path)
    rows = {r["name"]: dict(r) for r in list_sources(conn)}
    conn.close()
    assert rows["GoodFeed"]["last_status"] == "ok"
    assert rows["GoodFeed"]["last_error"] is None
    assert rows["BadFeed"]["last_status"] == "failed"
    assert rows["BadFeed"]["last_error"] is not None
