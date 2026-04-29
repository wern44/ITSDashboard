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
    record_run_finish,
    record_run_start,
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
    assert version >= 1
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


def test_record_run_start_returns_id(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    run_id = record_run_start(conn)
    assert isinstance(run_id, int)
    row = conn.execute(
        "SELECT started_at, finished_at, succeeded FROM generation_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["started_at"] is not None
    assert row["finished_at"] is None
    assert row["succeeded"] is None
    conn.close()


def test_record_run_finish_success(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    run_id = record_run_start(conn)
    record_run_finish(conn, run_id, succeeded=True, article_count=42, error=None)
    row = conn.execute(
        "SELECT finished_at, succeeded, article_count, error FROM generation_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["finished_at"] is not None
    assert row["succeeded"] == 1
    assert row["article_count"] == 42
    assert row["error"] is None
    conn.close()


def test_record_run_finish_failure(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    run_id = record_run_start(conn)
    record_run_finish(conn, run_id, succeeded=False, article_count=None, error="boom")
    row = conn.execute(
        "SELECT succeeded, article_count, error FROM generation_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["succeeded"] == 0
    assert row["article_count"] is None
    assert row["error"] == "boom"
    conn.close()


def test_load_briefing_by_date(tmp_path: Path) -> None:
    from its_briefing.db import load_briefing as db_load_briefing
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    a = _make_article()
    db_save_briefing(conn, _make_briefing(date(2026, 4, 27), [a]))
    db_save_briefing(conn, _make_briefing(date(2026, 4, 28), [a]))
    loaded = db_load_briefing(conn, date(2026, 4, 27))
    assert loaded is not None
    assert loaded.date == date(2026, 4, 27)
    assert db_load_briefing(conn, date(2026, 1, 1)) is None
    conn.close()


def test_init_schema_creates_sources_table(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "sources" in tables
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(sources)").fetchall()
    }
    assert cols >= {
        "id", "name", "url", "lang", "enabled",
        "last_status", "last_checked_at", "last_error", "last_diagnosis",
        "created_at", "updated_at",
    }
    conn.close()


def test_init_schema_bumps_to_v2(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version >= 2
    conn.close()


def test_init_schema_v1_to_v2_migration(tmp_path: Path) -> None:
    """A pre-existing v1 DB must upgrade in place without losing data."""
    db_path = tmp_path / "v1.db"
    conn = get_connection(db_path)
    # Create only the v1 schema (no sources table) and stamp version=1.
    conn.executescript("""
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        INSERT INTO schema_version (version) VALUES (1);
    """)
    conn.commit()
    conn.close()

    conn = get_connection(db_path)
    init_schema(conn)
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert version >= 2
    assert "sources" in tables


from its_briefing.db import (
    create_source,
    delete_source,
    get_source,
    list_sources,
    record_source_check_result,
    update_source,
)


def _seed_one(conn: sqlite3.Connection) -> int:
    return create_source(conn, name="Test", url="https://example.com/feed", lang="EN", enabled=True)


def test_create_source_returns_id(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    sid = _seed_one(conn)
    assert sid >= 1
    conn.close()


def test_create_source_unique_name(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    _seed_one(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _seed_one(conn)
    conn.close()


def test_list_sources_returns_all_when_no_filter(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    create_source(conn, name="A", url="https://a/", lang="EN", enabled=True)
    create_source(conn, name="B", url="https://b/", lang="DE", enabled=False)
    rows = list_sources(conn)
    assert {r["name"] for r in rows} == {"A", "B"}
    conn.close()


def test_list_sources_enabled_only(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    create_source(conn, name="A", url="https://a/", lang="EN", enabled=True)
    create_source(conn, name="B", url="https://b/", lang="DE", enabled=False)
    rows = list_sources(conn, enabled_only=True)
    assert [r["name"] for r in rows] == ["A"]
    conn.close()


def test_update_source_partial(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    sid = _seed_one(conn)
    update_source(conn, sid, {"enabled": False, "url": "https://new/"})
    row = get_source(conn, sid)
    assert row["enabled"] == 0
    assert row["url"] == "https://new/"
    assert row["name"] == "Test"  # untouched
    conn.close()


def test_update_source_unknown_field(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    sid = _seed_one(conn)
    with pytest.raises(KeyError):
        update_source(conn, sid, {"bogus": 1})
    conn.close()


def test_delete_source(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    sid = _seed_one(conn)
    delete_source(conn, sid)
    assert get_source(conn, sid) is None
    conn.close()


def test_record_source_check_result_clears_diagnosis_on_status_change(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    sid = _seed_one(conn)
    # Seed prior failed state with a diagnosis.
    record_source_check_result(conn, sid, status="failed", error="HTTP 503")
    update_source(conn, sid, {"last_diagnosis": "Likely transient. Retry."})
    # Now flip to ok.
    record_source_check_result(conn, sid, status="ok", error=None)
    row = get_source(conn, sid)
    assert row["last_status"] == "ok"
    assert row["last_error"] is None
    assert row["last_diagnosis"] is None
    conn.close()


def test_record_source_check_result_keeps_diagnosis_when_status_unchanged(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    sid = _seed_one(conn)
    record_source_check_result(conn, sid, status="failed", error="HTTP 503")
    update_source(conn, sid, {"last_diagnosis": "Likely CDN throttle"})
    record_source_check_result(conn, sid, status="failed", error="HTTP 503")
    row = get_source(conn, sid)
    assert row["last_diagnosis"] == "Likely CDN throttle"
    conn.close()


import yaml as _yaml
from its_briefing.db import seed_sources_from_yaml


def _write_sources_yaml(path: Path, items: list[dict]) -> Path:
    path.write_text(_yaml.safe_dump({"sources": items}), encoding="utf-8")
    return path


def test_seed_sources_from_yaml_inserts_when_empty(tmp_path: Path) -> None:
    yaml_path = _write_sources_yaml(tmp_path / "sources.yaml", [
        {"name": "A", "url": "https://a/", "lang": "EN"},
        {"name": "B", "url": "https://b/", "lang": "DE"},
    ])
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    seed_sources_from_yaml(conn, yaml_path)
    rows = list_sources(conn)
    assert {r["name"] for r in rows} == {"A", "B"}
    conn.close()


def test_seed_sources_from_yaml_idempotent(tmp_path: Path) -> None:
    yaml_path = _write_sources_yaml(tmp_path / "sources.yaml", [
        {"name": "A", "url": "https://a/", "lang": "EN"},
    ])
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    seed_sources_from_yaml(conn, yaml_path)
    seed_sources_from_yaml(conn, yaml_path)
    rows = list_sources(conn)
    assert len(rows) == 1
    conn.close()


def test_seed_sources_from_yaml_skips_when_table_nonempty(tmp_path: Path) -> None:
    yaml_path = _write_sources_yaml(tmp_path / "sources.yaml", [
        {"name": "A", "url": "https://a/", "lang": "EN"},
    ])
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    create_source(conn, name="Existing", url="https://e/", lang="EN", enabled=True)
    seed_sources_from_yaml(conn, yaml_path)
    rows = list_sources(conn)
    assert {r["name"] for r in rows} == {"Existing"}
    conn.close()
