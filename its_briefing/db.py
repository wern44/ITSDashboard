"""SQLite-backed persistence: connection helper, schema, and CRUD."""
from __future__ import annotations

import json
import sqlite3
from datetime import date as date_type, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from its_briefing.config import Settings
from its_briefing.models import Article, Briefing, ExecutiveSummary

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

CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    url             TEXT NOT NULL,
    lang            TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_status     TEXT,
    last_checked_at TEXT,
    last_error      TEXT,
    last_diagnosis  TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_enabled ON sources(enabled);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""

SCHEMA_VERSION = 2


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
    """Idempotent schema creation + forward migrations."""
    conn.executescript(_SCHEMA_SQL)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    current = row["version"] if row else 0
    if current == 0:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif current < SCHEMA_VERSION:
        # Forward migrations are pure-additive; CREATE TABLE IF NOT EXISTS in
        # _SCHEMA_SQL has already added the new tables. Just bump the version.
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
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


def upsert_article(
    conn: sqlite3.Connection, article: Article, first_seen: datetime
) -> None:
    """Insert or update an article. `first_seen` is set only on first insert."""
    conn.execute(
        """
        INSERT INTO articles
            (id, source, source_lang, title, link, published, summary, category, first_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source = excluded.source,
            source_lang = excluded.source_lang,
            title = excluded.title,
            link = excluded.link,
            published = excluded.published,
            summary = excluded.summary,
            category = excluded.category
        """,
        (
            article.id,
            article.source,
            article.source_lang,
            article.title,
            article.link,
            article.published.isoformat(),
            article.summary,
            article.category,
            first_seen.isoformat(),
        ),
    )


def save_briefing(conn: sqlite3.Connection, briefing: Briefing) -> None:
    """Persist a briefing in a single transaction.

    Upserts every article, upserts the briefing row, and replaces the join
    rows for that date.
    """
    try:
        first_seen = briefing.generated_at
        for a in briefing.articles:
            upsert_article(conn, a, first_seen=first_seen)

        conn.execute(
            """
            INSERT INTO briefings (date, generated_at, summary_json, failed_sources)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                generated_at = excluded.generated_at,
                summary_json = excluded.summary_json,
                failed_sources = excluded.failed_sources
            """,
            (
                briefing.date.isoformat(),
                briefing.generated_at.isoformat(),
                briefing.summary.model_dump_json(),
                json.dumps(briefing.failed_sources),
            ),
        )
        conn.execute(
            "DELETE FROM briefing_articles WHERE briefing_date = ?",
            (briefing.date.isoformat(),),
        )
        conn.executemany(
            "INSERT INTO briefing_articles (briefing_date, article_id) VALUES (?, ?)",
            [(briefing.date.isoformat(), a.id) for a in briefing.articles],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def load_briefing(
    conn: sqlite3.Connection, target_date: date_type
) -> Optional[Briefing]:
    """Return the briefing for a specific date, or None if absent."""
    row = conn.execute(
        "SELECT date, generated_at, summary_json, failed_sources FROM briefings WHERE date = ?",
        (target_date.isoformat(),),
    ).fetchone()
    if row is None:
        return None

    article_rows = conn.execute(
        """
        SELECT a.id, a.source, a.source_lang, a.title, a.link, a.published, a.summary, a.category
        FROM articles a
        JOIN briefing_articles ba ON ba.article_id = a.id
        WHERE ba.briefing_date = ?
        ORDER BY a.published DESC
        """,
        (target_date.isoformat(),),
    ).fetchall()

    articles = [
        Article(
            id=ar["id"],
            source=ar["source"],
            source_lang=ar["source_lang"],
            title=ar["title"],
            link=ar["link"],
            published=datetime.fromisoformat(ar["published"]),
            summary=ar["summary"],
            category=ar["category"],
        )
        for ar in article_rows
    ]

    return Briefing(
        date=target_date,
        generated_at=datetime.fromisoformat(row["generated_at"]),
        summary=ExecutiveSummary.model_validate_json(row["summary_json"]),
        articles=articles,
        failed_sources=json.loads(row["failed_sources"]),
        article_count=len(articles),
    )


def latest_briefing(conn: sqlite3.Connection) -> Optional[Briefing]:
    """Return the briefing with the highest date, or None if no briefings exist."""
    row = conn.execute(
        "SELECT date FROM briefings ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return load_briefing(conn, date_type.fromisoformat(row["date"]))


def record_run_start(conn: sqlite3.Connection) -> int:
    """Insert a generation_runs row for the start of a pipeline run, return its id."""
    started = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO generation_runs (started_at) VALUES (?)", (started,)
    )
    conn.commit()
    return int(cur.lastrowid)


def record_run_finish(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    succeeded: bool,
    article_count: Optional[int],
    error: Optional[str],
) -> None:
    """Update a run row with the outcome at the end of the pipeline."""
    conn.execute(
        "UPDATE generation_runs SET finished_at = ?, succeeded = ?, article_count = ?, error = ? "
        "WHERE id = ?",
        (
            datetime.now(timezone.utc).isoformat(),
            1 if succeeded else 0,
            article_count,
            error,
            run_id,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Sources CRUD
# ---------------------------------------------------------------------------

_SOURCE_UPDATABLE_FIELDS: tuple[str, ...] = (
    "name",
    "url",
    "lang",
    "enabled",
    "last_status",
    "last_checked_at",
    "last_error",
    "last_diagnosis",
)


def create_source(
    conn: sqlite3.Connection,
    *,
    name: str,
    url: str,
    lang: str,
    enabled: bool = True,
) -> int:
    """Insert a source row. Returns the new id. Raises IntegrityError on duplicate name."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO sources (name, url, lang, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, url, lang, 1 if enabled else 0, now, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_source(conn: sqlite3.Connection, source_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()


def list_sources(
    conn: sqlite3.Connection, *, enabled_only: bool = False
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM sources"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY name COLLATE NOCASE"
    return list(conn.execute(sql).fetchall())


def update_source(
    conn: sqlite3.Connection, source_id: int, partial: dict[str, Any]
) -> None:
    unknown = set(partial) - set(_SOURCE_UPDATABLE_FIELDS)
    if unknown:
        raise KeyError(f"unknown source fields: {sorted(unknown)}")
    if not partial:
        return
    columns = list(partial.keys())
    values: list[Any] = []
    for col in columns:
        v = partial[col]
        if col == "enabled" and isinstance(v, bool):
            v = 1 if v else 0
        values.append(v)
    values.append(datetime.now(timezone.utc).isoformat())  # updated_at
    values.append(source_id)
    set_clause = ", ".join(f"{c} = ?" for c in columns) + ", updated_at = ?"
    conn.execute(f"UPDATE sources SET {set_clause} WHERE id = ?", values)
    conn.commit()


def delete_source(conn: sqlite3.Connection, source_id: int) -> None:
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()


def record_source_check_result(
    conn: sqlite3.Connection,
    source_id: int,
    *,
    status: str,
    error: Optional[str],
) -> None:
    """Persist a health-check outcome. Clears last_diagnosis when status changes."""
    prev = conn.execute(
        "SELECT last_status FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    prev_status = prev["last_status"] if prev else None
    now = datetime.now(timezone.utc).isoformat()
    new_diag_clause = ", last_diagnosis = NULL" if prev_status != status else ""
    conn.execute(
        f"""
        UPDATE sources
        SET last_status = ?, last_checked_at = ?, last_error = ?, updated_at = ?{new_diag_clause}
        WHERE id = ?
        """,
        (status, now, error, now, source_id),
    )
    conn.commit()
