"""Tests for its_briefing.db."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from its_briefing.config import Settings
from its_briefing.db import (
    get_connection,
    get_settings,
    init_schema,
    seed_settings_from_env,
    update_settings,
)


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
