"""SQLite-backed persistence: connection helper, schema, and CRUD."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from its_briefing.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "cache" / "its_briefing.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    source_lang TEXT NOT NULL,
    title       TEXT NOT NULL,
    link        TEXT NOT NULL,
    published   TEXT NOT NULL,
    summary     TEXT NOT NULL,
    category    TEXT,
    first_seen  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published);

CREATE TABLE IF NOT EXISTS briefings (
    date           TEXT PRIMARY KEY,
    generated_at   TEXT NOT NULL,
    summary_json   TEXT NOT NULL,
    failed_sources TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS briefing_articles (
    briefing_date TEXT NOT NULL,
    article_id    TEXT NOT NULL,
    PRIMARY KEY (briefing_date, article_id),
    FOREIGN KEY (briefing_date) REFERENCES briefings(date) ON DELETE CASCADE,
    FOREIGN KEY (article_id)    REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS generation_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    succeeded     INTEGER,
    article_count INTEGER,
    error         TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""

SCHEMA_VERSION = 1


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open the SQLite database, creating its parent dir if needed.

    Enables foreign keys and WAL journaling. Returns a connection with
    sqlite3.Row factory so rows can be accessed by column name.

    Note: when `db_path` is None, DEFAULT_DB_PATH is looked up at call time
    (not at function-definition time) so tests can monkeypatch the module
    attribute and have it take effect.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation. Sets schema_version to 1 on first init."""
    conn.executescript(_SCHEMA_SQL)
    existing = conn.execute("SELECT version FROM schema_version").fetchone()
    if existing is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()


_SETTINGS_KEYS: tuple[str, ...] = (
    "llm_provider",
    "llm_base_url",
    "llm_model",
    "timezone",
    "schedule_hour",
    "schedule_minute",
    "flask_host",
    "flask_port",
    "log_level",
)


def seed_settings_from_env(conn: sqlite3.Connection, env_settings: Settings) -> None:
    """Populate `settings` from env-derived defaults if (and only if) the table is empty."""
    count = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    if count > 0:
        return
    payload = {k: getattr(env_settings, k) for k in _SETTINGS_KEYS}
    conn.executemany(
        "INSERT INTO settings (key, value) VALUES (?, ?)",
        [(k, json.dumps(v)) for k, v in payload.items()],
    )
    conn.commit()


def get_settings(conn: sqlite3.Connection) -> Settings:
    """Read current settings from the DB. Raises if not seeded."""
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    if not rows:
        raise RuntimeError("settings table is empty; call seed_settings_from_env() first")
    data: dict[str, Any] = {row["key"]: json.loads(row["value"]) for row in rows}
    return Settings(**{k: data[k] for k in _SETTINGS_KEYS})


def update_settings(conn: sqlite3.Connection, partial: dict[str, Any]) -> None:
    """Upsert one or more settings keys. Raises KeyError on unknown keys."""
    unknown = set(partial) - set(_SETTINGS_KEYS)
    if unknown:
        raise KeyError(f"unknown settings keys: {sorted(unknown)}")
    conn.executemany(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [(k, json.dumps(v)) for k, v in partial.items()],
    )
    conn.commit()
