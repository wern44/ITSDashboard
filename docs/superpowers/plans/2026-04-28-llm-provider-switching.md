# LLM Provider Switching + SQLite Persistence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the `'message'` KeyError when using LM Studio, add a `/settings` page to switch between Ollama and LM Studio at runtime, and migrate all runtime state (settings, articles, briefings, runs log) into a flat-file SQLite database at `cache/its_briefing.db`.

**Architecture:** A new `db.py` module owns the SQLite connection and schema. `Settings` gains `llm_provider`, `llm_base_url`, `llm_model` fields and is seeded from `.env` on first boot, then becomes DB-backed. `llm.py` is split into `OllamaClient` and `LMStudioClient` selected at call time via `make_client(settings)`. `storage.py` keeps its public API but writes to the DB. New routes `/settings` (GET/POST) and `/api/test-connection` (POST) drive the UI.

**Tech Stack:** Python 3.13, Flask, APScheduler, httpx, Pydantic v2, sqlite3 (stdlib), pytest, pytest-httpx, freezegun.

**Spec:** `docs/superpowers/specs/2026-04-28-llm-provider-switching-design.md`

---

## File Structure

**Created:**
- `its_briefing/db.py` — SQLite connection, schema init, CRUD helpers
- `its_briefing/templates/settings.html` — settings form
- `tests/test_db.py` — DB unit tests
- `tests/test_app.py` — route tests for /settings and /api/test-connection

**Modified:**
- `its_briefing/config.py` — new `Settings` fields and env aliases
- `its_briefing/llm.py` — split into provider clients
- `its_briefing/storage.py` — DB-backed implementation
- `its_briefing/scheduler.py` — add `reschedule()`
- `its_briefing/generate.py` — read settings from DB, write run rows
- `its_briefing/__main__.py` — init schema and seed on startup
- `its_briefing/app.py` — new routes + settings link
- `its_briefing/templates/briefing.html` — gear icon linking to `/settings`
- `tests/test_config.py` — new field assertions, seed-once test
- `tests/test_llm.py` — parameterized across both clients
- `tests/test_storage.py` — DB-backed assertions
- `tests/test_generate.py` — assert run rows recorded
- `.env.example` — new `LLM_*` keys with comments
- `CLAUDE.md` — DB-as-truth + settings UI section

---

## Task 1: Extend `Settings` with LLM provider fields

**Files:**
- Modify: `its_briefing/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Read current test file**

Run: `cat tests/test_config.py`

- [ ] **Step 2: Write failing tests for new fields and aliases**

Add to `tests/test_config.py`:

```python
import pytest
from its_briefing.config import Settings


def test_settings_from_env_uses_new_llm_keys(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "lmstudio")
    monkeypatch.setenv("LLM_BASE_URL", "http://192.168.32.231:1234")
    monkeypatch.setenv("LLM_MODEL", "google/gemma-4-26b-a4b")
    s = Settings.from_env()
    assert s.llm_provider == "lmstudio"
    assert s.llm_base_url == "http://192.168.32.231:1234"
    assert s.llm_model == "google/gemma-4-26b-a4b"


def test_settings_from_env_falls_back_to_legacy_ollama_keys(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://legacy:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")
    s = Settings.from_env()
    assert s.llm_provider == "ollama"  # default
    assert s.llm_base_url == "http://legacy:11434"
    assert s.llm_model == "llama3.1:8b"


def test_settings_provider_must_be_known(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_settings_defaults(monkeypatch):
    for k in ("LLM_PROVIDER","LLM_BASE_URL","LLM_MODEL","OLLAMA_BASE_URL","OLLAMA_MODEL"):
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env()
    assert s.llm_provider == "ollama"
    assert s.llm_base_url == "http://localhost:11434"
    assert s.llm_model == "llama3.1:8b"
```

- [ ] **Step 3: Run new tests; confirm failure**

Run: `pytest tests/test_config.py -v`
Expected: 4 new tests fail with `AttributeError` on `llm_provider`/`llm_base_url`/`llm_model`.

- [ ] **Step 4: Update `Settings` to add the three fields with env aliases**

Replace the body of `its_briefing/config.py` `Settings` class with:

```python
from typing import Literal

class Settings(BaseModel):
    llm_provider: Literal["ollama", "lmstudio"] = "ollama"
    llm_base_url: str
    llm_model: str
    timezone: str
    schedule_hour: int
    schedule_minute: int
    flask_host: str
    flask_port: int
    log_level: str

    # Legacy aliases used by older tests / fixtures. Prefer llm_base_url / llm_model.
    @property
    def ollama_base_url(self) -> str:
        return self.llm_base_url

    @property
    def ollama_model(self) -> str:
        return self.llm_model

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.environ.get("LLM_PROVIDER", "ollama")
        if provider not in ("ollama", "lmstudio"):
            raise ValueError(f"LLM_PROVIDER must be 'ollama' or 'lmstudio', got {provider!r}")
        base_url = (
            os.environ.get("LLM_BASE_URL")
            or os.environ.get("OLLAMA_BASE_URL")
            or "http://localhost:11434"
        )
        model = (
            os.environ.get("LLM_MODEL")
            or os.environ.get("OLLAMA_MODEL")
            or "llama3.1:8b"
        )
        return cls(
            llm_provider=provider,
            llm_base_url=base_url,
            llm_model=model,
            timezone=os.environ.get("TIMEZONE", "Europe/Berlin"),
            schedule_hour=int(os.environ.get("SCHEDULE_HOUR", "6")),
            schedule_minute=int(os.environ.get("SCHEDULE_MINUTE", "0")),
            flask_host=os.environ.get("FLASK_HOST", "127.0.0.1"),
            flask_port=int(os.environ.get("FLASK_PORT", "8089")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
```

- [ ] **Step 5: Run all config tests; confirm pass**

Run: `pytest tests/test_config.py -v`
Expected: all tests pass.

- [ ] **Step 6: Run full test suite to confirm no regressions**

Run: `pytest`
Expected: 22+ tests pass (test_llm.py still passes because `ollama_base_url`/`ollama_model` are aliased properties).

- [ ] **Step 7: Commit**

```bash
git add its_briefing/config.py tests/test_config.py
git commit -m "feat(config): add llm_provider/llm_base_url/llm_model with legacy aliases"
```

---

## Task 2: Create `db.py` — connection + schema init

**Files:**
- Create: `its_briefing/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing test for connection + schema init**

Create `tests/test_db.py`:

```python
"""Tests for its_briefing.db."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from its_briefing.db import get_connection, init_schema


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
```

- [ ] **Step 2: Run; confirm import error**

Run: `pytest tests/test_db.py -v`
Expected: collection error (`ModuleNotFoundError: its_briefing.db`).

- [ ] **Step 3: Create `its_briefing/db.py` with connection + schema**

Create `its_briefing/db.py`:

```python
"""SQLite-backed persistence: connection helper, schema, and CRUD."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

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
```

- [ ] **Step 4: Run; confirm pass**

Run: `pytest tests/test_db.py -v`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/db.py tests/test_db.py
git commit -m "feat(db): add SQLite connection + schema init"
```

---

## Task 3: `db.py` — settings CRUD + seed-from-env

**Files:**
- Modify: `its_briefing/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Add failing tests for settings round-trip and seed-once**

Append to `tests/test_db.py`:

```python
from its_briefing.config import Settings
from its_briefing.db import (
    get_settings,
    seed_settings_from_env,
    update_settings,
)


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
```

- [ ] **Step 2: Run; confirm failure**

Run: `pytest tests/test_db.py -v`
Expected: 4 new tests fail (`ImportError` on `get_settings`/`seed_settings_from_env`/`update_settings`).

- [ ] **Step 3: Implement settings CRUD in `db.py`**

Append to `its_briefing/db.py`:

```python
import json
from typing import Any

from its_briefing.config import Settings

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
```

- [ ] **Step 4: Run; confirm pass**

Run: `pytest tests/test_db.py -v`
Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/db.py tests/test_db.py
git commit -m "feat(db): settings CRUD + seed-from-env"
```

---

## Task 4: `db.py` — articles, briefings, briefing_articles CRUD

**Files:**
- Modify: `its_briefing/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_db.py`:

```python
from datetime import date, datetime, timezone

from its_briefing.db import (
    latest_briefing as db_latest_briefing,
    save_briefing as db_save_briefing,
    upsert_article,
)
from its_briefing.models import Article, Briefing, Bullet, ExecutiveSummary


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
```

- [ ] **Step 2: Run; confirm failure**

Run: `pytest tests/test_db.py -v`
Expected: new tests fail (`ImportError`).

- [ ] **Step 3: Implement article + briefing CRUD**

Append to `its_briefing/db.py`:

```python
from datetime import date as date_type, datetime
from typing import Optional

from its_briefing.models import Article, Briefing, ExecutiveSummary


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


def latest_briefing(conn: sqlite3.Connection) -> Optional[Briefing]:
    """Return the briefing with the highest date, or None if no briefings exist."""
    row = conn.execute(
        "SELECT date, generated_at, summary_json, failed_sources FROM briefings "
        "ORDER BY date DESC LIMIT 1"
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
        (row["date"],),
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
        date=date_type.fromisoformat(row["date"]),
        generated_at=datetime.fromisoformat(row["generated_at"]),
        summary=ExecutiveSummary.model_validate_json(row["summary_json"]),
        articles=articles,
        failed_sources=json.loads(row["failed_sources"]),
        article_count=len(articles),
    )
```

- [ ] **Step 4: Run; confirm pass**

Run: `pytest tests/test_db.py -v`
Expected: 12 tests pass.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/db.py tests/test_db.py
git commit -m "feat(db): article and briefing CRUD"
```

---

## Task 5: `db.py` — generation_runs CRUD

**Files:**
- Modify: `its_briefing/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_db.py`:

```python
from its_briefing.db import record_run_finish, record_run_start


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
```

- [ ] **Step 2: Run; confirm failure**

Run: `pytest tests/test_db.py -v`
Expected: new tests fail (`ImportError`).

- [ ] **Step 3: Implement runs CRUD**

Append to `its_briefing/db.py`:

```python
from datetime import timezone


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
```

- [ ] **Step 4: Run; confirm pass**

Run: `pytest tests/test_db.py -v`
Expected: 15 tests pass.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/db.py tests/test_db.py
git commit -m "feat(db): generation_runs CRUD"
```

---

## Task 6: Refactor `llm.py` — `OllamaClient` and `LMStudioClient`

**Files:**
- Modify: `its_briefing/llm.py`
- Modify: `tests/test_llm.py`

- [ ] **Step 1: Replace `tests/test_llm.py` with parameterized tests**

Overwrite `tests/test_llm.py`:

```python
"""Tests for its_briefing.llm — both Ollama and LM Studio clients."""
import json
from datetime import date, datetime, timezone

import pytest
from pytest_httpx import HTTPXMock

from its_briefing.config import Category, Settings
from its_briefing.llm import (
    LLMClientError,
    LMStudioClient,
    OllamaClient,
    build_summary,
    classify_article,
    make_client,
)
from its_briefing.models import Article, ExecutiveSummary


def _article() -> Article:
    return Article(
        id="abc123",
        source="Test",
        source_lang="EN",
        title="Critical zero-day in Foo software",
        link="https://example.com/x",
        published=datetime(2026, 4, 7, 10, 0, tzinfo=timezone.utc),
        summary="A new 0day was disclosed.",
    )


def _categories() -> list[Category]:
    return [
        Category(name="0-Day", description="Zero-days"),
        Category(name="Hacks", description="Breaches"),
        Category(name="Regulation", description="Compliance"),
    ]


def _settings(provider: str, base_url: str) -> Settings:
    return Settings(
        llm_provider=provider,
        llm_base_url=base_url,
        llm_model="test-model",
        timezone="Europe/Berlin",
        schedule_hour=6,
        schedule_minute=0,
        flask_host="127.0.0.1",
        flask_port=8089,
        log_level="INFO",
    )


# ---------- provider matrix used by parameterized tests ----------

OLLAMA = ("ollama", "http://localhost:11434", "/api/chat")
LMSTUDIO = ("lmstudio", "http://localhost:1234", "/v1/chat/completions")


def _success_response(provider: str, content: str) -> dict:
    if provider == "ollama":
        return {"message": {"content": content}}
    return {"choices": [{"message": {"content": content}}]}


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_classify_article_returns_chosen_category(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(
        url=f"{base_url}{path}",
        json=_success_response(provider, '{"category": "0-Day"}'),
    )
    result = classify_article(_article(), _categories(), _settings(provider, base_url))
    assert result == "0-Day"


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_classify_article_unknown_category_falls_back(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(
        url=f"{base_url}{path}",
        json=_success_response(provider, '{"category": "Bogus"}'),
    )
    assert classify_article(_article(), _categories(), _settings(provider, base_url)) == "Uncategorized"


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_classify_article_invalid_json_falls_back(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(
        url=f"{base_url}{path}",
        json=_success_response(provider, "this is not json"),
    )
    assert classify_article(_article(), _categories(), _settings(provider, base_url)) == "Uncategorized"


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_classify_article_http_error_falls_back(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(url=f"{base_url}{path}", status_code=500)
    assert classify_article(_article(), _categories(), _settings(provider, base_url)) == "Uncategorized"


def test_classify_article_lmstudio_missing_choices_key_falls_back(
    httpx_mock: HTTPXMock,
) -> None:
    """Locks the bug fix: an LM Studio response without 'choices' must not raise."""
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"unexpected": "shape"},
    )
    assert (
        classify_article(_article(), _categories(), _settings("lmstudio", "http://localhost:1234"))
        == "Uncategorized"
    )


# ---------- summary tests ----------

def _articles() -> list[Article]:
    return [
        Article(
            id="id1",
            source="Test",
            source_lang="EN",
            title="CVE-2026-0001 critical RCE in WidgetServer",
            link="https://example.com/1",
            published=datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc),
            summary="A critical RCE was disclosed.",
            category="0-Day",
        ),
        Article(
            id="id2",
            source="Test",
            source_lang="EN",
            title="Ransomware hits hospital chain",
            link="https://example.com/2",
            published=datetime(2026, 4, 7, 8, 0, tzinfo=timezone.utc),
            summary="A ransomware group attacked.",
            category="Hacks",
        ),
    ]


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_build_summary_parses_structured_response(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    structured = {
        "critical_vulnerabilities": [
            {"text": "CVE-2026-0001 RCE in WidgetServer", "article_ids": ["id1"]}
        ],
        "active_threats": [],
        "notable_incidents": [
            {"text": "Hospital chain hit by ransomware", "article_ids": ["id2"]}
        ],
        "strategic_policy": [],
    }
    httpx_mock.add_response(
        url=f"{base_url}{path}",
        json=_success_response(provider, json.dumps(structured)),
    )
    s = build_summary(_articles(), _settings(provider, base_url), target_date=date(2026, 4, 7))
    assert isinstance(s, ExecutiveSummary)
    assert s.critical_vulnerabilities[0].text.startswith("CVE-2026-0001")


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_build_summary_invalid_json_falls_back(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(url=f"{base_url}{path}", json=_success_response(provider, "garbage"))
    httpx_mock.add_response(url=f"{base_url}{path}", json=_success_response(provider, "garbage"))
    s = build_summary(_articles(), _settings(provider, base_url), target_date=date(2026, 4, 7))
    assert s.critical_vulnerabilities[0].text.startswith("AI summary unavailable")


@pytest.mark.parametrize("provider,base_url,path", [OLLAMA, LMSTUDIO])
def test_build_summary_http_error_falls_back(
    httpx_mock: HTTPXMock, provider: str, base_url: str, path: str
) -> None:
    httpx_mock.add_response(url=f"{base_url}{path}", status_code=500)
    httpx_mock.add_response(url=f"{base_url}{path}", status_code=500)
    s = build_summary(_articles(), _settings(provider, base_url), target_date=date(2026, 4, 7))
    assert s.critical_vulnerabilities[0].text.startswith("AI summary unavailable")


# ---------- list_models tests ----------

def test_ollama_list_models(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/tags",
        json={"models": [{"name": "llama3.1:8b"}, {"name": "mistral:7b"}]},
    )
    client = OllamaClient("http://localhost:11434", "llama3.1:8b")
    assert client.list_models() == ["llama3.1:8b", "mistral:7b"]


def test_lmstudio_list_models(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/models",
        json={"data": [{"id": "google/gemma-4-26b-a4b"}, {"id": "qwen/qwen2.5"}]},
    )
    client = LMStudioClient("http://localhost:1234", "google/gemma-4-26b-a4b")
    assert client.list_models() == ["google/gemma-4-26b-a4b", "qwen/qwen2.5"]


def test_make_client_dispatches_on_provider() -> None:
    s_ollama = _settings("ollama", "http://localhost:11434")
    s_lm = _settings("lmstudio", "http://localhost:1234")
    assert isinstance(make_client(s_ollama), OllamaClient)
    assert isinstance(make_client(s_lm), LMStudioClient)
```

- [ ] **Step 2: Run; confirm failure**

Run: `pytest tests/test_llm.py -v`
Expected: most tests fail because `OllamaClient`, `LMStudioClient`, `make_client`, `LLMClientError` don't exist yet.

- [ ] **Step 3: Rewrite `its_briefing/llm.py`**

Overwrite `its_briefing/llm.py`:

```python
"""LLM clients (Ollama + LM Studio) and the classify/summarize entry points."""
from __future__ import annotations

import json
import logging
from datetime import date

import httpx
from pydantic import ValidationError

from its_briefing.config import Category, Settings
from its_briefing.models import Article, ExecutiveSummary

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 60
UNCATEGORIZED = "Uncategorized"


class LLMClientError(Exception):
    """Raised by an LLM client when a chat call fails for any reason."""


class OllamaClient:
    """Client for Ollama's native /api/chat endpoint."""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(self, prompt: str) -> str:
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "format": "json",
                    "stream": False,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=LLM_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            return data["message"]["content"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise LLMClientError(str(exc)) from exc

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            return [m["name"] for m in response.json().get("models", [])]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise LLMClientError(str(exc)) from exc


class LMStudioClient:
    """Client for LM Studio's OpenAI-compatible /v1/chat/completions endpoint."""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(self, prompt: str) -> str:
        try:
            response = httpx.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": self.model,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=LLM_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError) as exc:
            raise LLMClientError(str(exc)) from exc

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.base_url}/v1/models", timeout=5)
            response.raise_for_status()
            return [m["id"] for m in response.json().get("data", [])]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise LLMClientError(str(exc)) from exc


LLMClient = OllamaClient | LMStudioClient


def make_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "ollama":
        return OllamaClient(settings.llm_base_url, settings.llm_model)
    return LMStudioClient(settings.llm_base_url, settings.llm_model)


def _classification_prompt(article: Article, categories: list[Category]) -> str:
    cat_lines = "\n".join(f"- {c.name}: {c.description}" for c in categories)
    return (
        "You are a cybersecurity news classifier. Pick exactly ONE category for the article.\n\n"
        f"Categories:\n{cat_lines}\n\n"
        f"Article title: {article.title}\n"
        f"Article summary: {article.summary[:500]}\n\n"
        'Respond with JSON only: {"category": "<one of the names above>"}'
    )


def classify_article(
    article: Article, categories: list[Category], settings: Settings
) -> str:
    """Classify a single article into one of the configured categories."""
    valid_names = {c.name for c in categories}
    client = make_client(settings)
    try:
        content = client.chat(_classification_prompt(article, categories))
        parsed = json.loads(content)
        chosen = parsed.get("category", "")
    except (LLMClientError, json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Classification failed for %s: %s", article.id, exc)
        return UNCATEGORIZED

    if chosen not in valid_names:
        logger.warning("Classifier returned unknown category %r for %s", chosen, article.id)
        return UNCATEGORIZED
    return chosen


def _summary_prompt(articles: list[Article]) -> str:
    article_lines = []
    for a in articles:
        cat = a.category or "Uncategorized"
        snippet = a.summary[:300].replace("\n", " ")
        article_lines.append(f"[{a.id}] ({cat}) {a.title} — {snippet}")
    article_block = "\n".join(article_lines)
    return (
        "You are a cybersecurity briefing analyst. Read the articles below and produce an "
        "executive summary in four sections.\n\n"
        "Each section is a list of bullets. Each bullet has a short text (1-2 sentences) and a "
        "list of article_ids that support it. Use the bracketed [id] from each article line.\n\n"
        "Sections:\n"
        "- critical_vulnerabilities: CVEs, advisories, urgent patches\n"
        "- active_threats: ongoing campaigns, malware, threat actor activity\n"
        "- notable_incidents: confirmed breaches, ransomware victims, leaks\n"
        "- strategic_policy: regulation, geopolitics, industry trends\n\n"
        "Empty sections are allowed (return an empty list). Be concise.\n\n"
        f"Articles:\n{article_block}\n\n"
        'Respond with JSON only, matching this exact shape:\n'
        '{"critical_vulnerabilities":[{"text":"...","article_ids":["..."]}],'
        '"active_threats":[],"notable_incidents":[],"strategic_policy":[]}'
    )


def _try_build_summary(articles: list[Article], settings: Settings) -> ExecutiveSummary:
    client = make_client(settings)
    content = client.chat(_summary_prompt(articles))
    parsed = json.loads(content)
    return ExecutiveSummary.model_validate(parsed)


def build_summary(
    articles: list[Article], settings: Settings, target_date: date
) -> ExecutiveSummary:
    """Build the executive summary, with one retry and a placeholder fallback."""
    for attempt in (1, 2):
        try:
            return _try_build_summary(articles, settings)
        except (LLMClientError, json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            logger.warning("Summary attempt %d failed: %s", attempt, exc)
    return ExecutiveSummary.placeholder(target_date)
```

- [ ] **Step 4: Run llm tests; confirm pass**

Run: `pytest tests/test_llm.py -v`
Expected: all tests pass (count: ~21 — 10 parameterized × 2 providers + 1 LM Studio bug-lock + 2 list_models + 1 make_client).

- [ ] **Step 5: Run full suite; confirm no regressions**

Run: `pytest`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add its_briefing/llm.py tests/test_llm.py
git commit -m "fix(llm): add LMStudioClient with OpenAI-compatible response parsing

Splits the LLM client into OllamaClient and LMStudioClient, fixing the
KeyError 'message' that occurred when LLM_BASE_URL pointed at LM Studio.
make_client(settings) selects the right client per provider."
```

---

## Task 7: Rewrite `storage.py` against the DB

**Files:**
- Modify: `its_briefing/storage.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Read existing storage tests**

Run: `cat tests/test_storage.py`

- [ ] **Step 2: Replace `tests/test_storage.py`**

Overwrite `tests/test_storage.py`:

```python
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
    # initialize schema once via a connection; storage helpers will reopen it as needed
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
```

- [ ] **Step 3: Run; confirm failure**

Run: `pytest tests/test_storage.py -v`
Expected: tests fail (`save_briefing` signature differs).

- [ ] **Step 4: Rewrite `its_briefing/storage.py`**

Overwrite `its_briefing/storage.py`:

```python
"""Persist briefings via SQLite. Public API kept stable for app.py / generate.py."""
from __future__ import annotations

from datetime import date as date_type
from pathlib import Path
from typing import Optional

from its_briefing import db
from its_briefing.models import Briefing


def save_briefing(briefing: Briefing, db_path: Path = db.DEFAULT_DB_PATH) -> None:
    """Persist a briefing to the SQLite database."""
    conn = db.get_connection(db_path)
    try:
        db.init_schema(conn)
        db.save_briefing(conn, briefing)
    finally:
        conn.close()


def load_briefing(
    target_date: date_type, db_path: Path = db.DEFAULT_DB_PATH
) -> Optional[Briefing]:
    """Return the briefing for a specific date, or None if absent."""
    conn = db.get_connection(db_path)
    try:
        db.init_schema(conn)
        latest = db.latest_briefing(conn)
        if latest is not None and latest.date == target_date:
            return latest
        # Fall back to a date-keyed query for non-latest briefings.
        row = conn.execute(
            "SELECT date FROM briefings WHERE date = ?", (target_date.isoformat(),)
        ).fetchone()
        if row is None:
            return None
        # Re-use latest_briefing's loading by temporarily ordering — simplest approach:
        # delete-and-reload pattern is overkill; just load via a small inline query.
        return _load_briefing_for_date(conn, target_date)
    finally:
        conn.close()


def latest_briefing(db_path: Path = db.DEFAULT_DB_PATH) -> Optional[Briefing]:
    """Return the most-recent briefing, or None if no briefings exist."""
    conn = db.get_connection(db_path)
    try:
        db.init_schema(conn)
        return db.latest_briefing(conn)
    finally:
        conn.close()


def _load_briefing_for_date(conn, target_date: date_type) -> Optional[Briefing]:
    """Load a briefing for a specific (non-latest) date. Mirrors db.latest_briefing."""
    import json
    from datetime import datetime
    from its_briefing.models import Article, ExecutiveSummary

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
```

- [ ] **Step 5: Run storage tests; confirm pass**

Run: `pytest tests/test_storage.py -v`
Expected: all pass.

- [ ] **Step 6: Run full suite to flag downstream breakage**

Run: `pytest`
Expected: `tests/test_generate.py` may now fail because it patches `storage.save_briefing` differently — note any failures, fix in Task 8.

- [ ] **Step 7: Commit**

```bash
git add its_briefing/storage.py tests/test_storage.py
git commit -m "feat(storage): DB-backed briefings; preserve public API"
```

---

## Task 8: Update `generate.py` — DB settings + run rows

**Files:**
- Modify: `its_briefing/generate.py`
- Modify: `tests/test_generate.py`

- [ ] **Step 1: Read existing generate tests**

Run: `cat tests/test_generate.py`

- [ ] **Step 2: Update `tests/test_generate.py` to assert run rows are recorded**

Add this test to `tests/test_generate.py` (keep existing tests, adapt patches as needed):

```python
from its_briefing.db import get_connection, init_schema, seed_settings_from_env
from its_briefing.config import Settings


def _seed_db(db_path):
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


def test_run_records_generation_run_on_success(tmp_path, monkeypatch):
    from its_briefing import generate

    db_path = tmp_path / "test.db"
    _seed_db(db_path)
    monkeypatch.setattr("its_briefing.generate.db.DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr("its_briefing.storage.db.DEFAULT_DB_PATH", db_path)

    monkeypatch.setattr(generate.fetch, "fetch_all", lambda sources: ([], []))
    monkeypatch.setattr(generate.llm, "classify_article", lambda *a, **k: "Uncategorized")
    monkeypatch.setattr(
        generate.llm,
        "build_summary",
        lambda articles, settings, target_date: __import__(
            "its_briefing.models", fromlist=["ExecutiveSummary"]
        ).ExecutiveSummary(),
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


def test_run_records_failed_run_on_exception(tmp_path, monkeypatch):
    from its_briefing import generate

    db_path = tmp_path / "test.db"
    _seed_db(db_path)
    monkeypatch.setattr("its_briefing.generate.db.DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr("its_briefing.storage.db.DEFAULT_DB_PATH", db_path)

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
```

- [ ] **Step 3: Run; confirm failure**

Run: `pytest tests/test_generate.py -v`
Expected: new tests fail; existing ones may also fail because settings come from env, not DB.

- [ ] **Step 4: Update `its_briefing/generate.py`**

Overwrite `its_briefing/generate.py`:

```python
"""Pipeline orchestrator: fetch -> classify -> summarize -> save."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from its_briefing import config, db, fetch, llm, storage
from its_briefing.models import Briefing

logger = logging.getLogger(__name__)


def run() -> Optional[Briefing]:
    """Run the full briefing pipeline. Returns the saved Briefing or None on failure."""
    load_dotenv()
    conn = db.get_connection()
    db.init_schema(conn)
    # First-boot seed (no-op once populated).
    db.seed_settings_from_env(conn, config.Settings.from_env())
    settings = db.get_settings(conn)
    run_id = db.record_run_start(conn)
    conn.close()

    try:
        sources = config.load_sources()
        categories = config.load_categories()

        logger.info("Fetching %d sources...", len(sources))
        articles, failed_sources = fetch.fetch_all(sources)
        logger.info(
            "Fetched %d articles, %d sources failed", len(articles), len(failed_sources)
        )

        for article in articles:
            article.category = llm.classify_article(article, categories, settings)

        now = datetime.now(timezone.utc)
        target_date = now.date()
        summary = llm.build_summary(articles, settings, target_date=target_date)

        briefing = Briefing(
            date=target_date,
            generated_at=now,
            summary=summary,
            articles=articles,
            failed_sources=failed_sources,
            article_count=len(articles),
        )

        storage.save_briefing(briefing)

        finish_conn = db.get_connection()
        db.record_run_finish(
            finish_conn,
            run_id,
            succeeded=True,
            article_count=briefing.article_count,
            error=None,
        )
        finish_conn.close()

        logger.info(
            "Briefing for %s generated: %d articles, %d failed sources",
            target_date.isoformat(),
            briefing.article_count,
            len(failed_sources),
        )
        return briefing

    except Exception as exc:  # noqa: BLE001 -- top-level guard
        logger.exception("Briefing generation failed")
        finish_conn = db.get_connection()
        db.record_run_finish(
            finish_conn,
            run_id,
            succeeded=False,
            article_count=None,
            error=f"{type(exc).__name__}: {exc}",
        )
        finish_conn.close()
        return None


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    run()
```

- [ ] **Step 5: Run; confirm pass**

Run: `pytest tests/test_generate.py -v`
Expected: all pass.

- [ ] **Step 6: Run full suite**

Run: `pytest`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add its_briefing/generate.py tests/test_generate.py
git commit -m "feat(generate): read settings from DB; record runs in generation_runs"
```

---

## Task 9: Add `scheduler.reschedule()`

**Files:**
- Modify: `its_briefing/scheduler.py`

- [ ] **Step 1: Add `reschedule()` to `scheduler.py`**

Append to `its_briefing/scheduler.py`:

```python
def reschedule(hour: int, minute: int, tz: str) -> None:
    """Replace the daily_briefing trigger with a new cron at the given time/timezone.

    Raises ValueError if the timezone string is invalid.
    """
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        raise RuntimeError("scheduler not running; call start() first")
    trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)  # raises on bad tz
    _scheduler.reschedule_job("daily_briefing", trigger=trigger)
    logger.info(
        "Scheduler rescheduled to %02d:%02d %s; next run %s",
        hour,
        minute,
        tz,
        _scheduler.get_job("daily_briefing").next_run_time,
    )
```

- [ ] **Step 2: Manual smoke check that the import still works**

Run: `python -c "from its_briefing import scheduler; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add its_briefing/scheduler.py
git commit -m "feat(scheduler): add reschedule(hour, minute, tz)"
```

---

## Task 10: Update `__main__.py` — schema init + seed on startup

**Files:**
- Modify: `its_briefing/__main__.py`

- [ ] **Step 1: Replace `its_briefing/__main__.py`**

Overwrite:

```python
"""Process entry point: starts Flask + APScheduler in one process.

Usage:
    python -m its_briefing
"""
from __future__ import annotations

import logging
import signal
import sys

from dotenv import load_dotenv

from its_briefing import db, scheduler
from its_briefing.app import create_app
from its_briefing.config import Settings


def main() -> None:
    load_dotenv()
    env_settings = Settings.from_env()

    logging.basicConfig(
        level=getattr(logging, env_settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # First-boot DB init + settings seed.
    conn = db.get_connection()
    db.init_schema(conn)
    db.seed_settings_from_env(conn, env_settings)
    settings = db.get_settings(conn)
    conn.close()

    scheduler.start(settings)

    def _graceful_exit(signum, frame):  # noqa: ARG001
        logging.info("Shutting down…")
        scheduler.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_exit)
    signal.signal(signal.SIGTERM, _graceful_exit)

    app = create_app()
    # flask_host/flask_port are process-bound; read from env_settings (env always wins).
    app.run(host=env_settings.flask_host, port=env_settings.flask_port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test the import path**

Run: `python -c "from its_briefing import __main__; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add its_briefing/__main__.py
git commit -m "feat(main): init schema and seed settings on startup"
```

---

## Task 11: Add `GET /settings` route + `settings.html`

**Files:**
- Create: `its_briefing/templates/settings.html`
- Modify: `its_briefing/app.py`
- Modify: `its_briefing/templates/briefing.html`
- Create: `tests/test_app.py`

- [ ] **Step 1: Write failing test for `GET /settings`**

Create `tests/test_app.py`:

```python
"""Tests for app.py routes."""
from __future__ import annotations

from pathlib import Path

import pytest

from its_briefing import db
from its_briefing.app import create_app
from its_briefing.config import Settings


@pytest.fixture
def app(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setattr("its_briefing.db.DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr("its_briefing.storage.db.DEFAULT_DB_PATH", db_path)

    conn = db.get_connection(db_path)
    db.init_schema(conn)
    db.seed_settings_from_env(
        conn,
        Settings(
            llm_provider="lmstudio",
            llm_base_url="http://192.168.32.231:1234",
            llm_model="google/gemma-4-26b-a4b",
            timezone="Europe/Berlin",
            schedule_hour=7,
            schedule_minute=30,
            flask_host="127.0.0.1",
            flask_port=8089,
            log_level="INFO",
        ),
    )
    conn.close()

    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_get_settings_renders_current_values(client):
    r = client.get("/settings")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "lmstudio" in body
    assert "192.168.32.231" in body
    assert "google/gemma-4-26b-a4b" in body
    assert "Europe/Berlin" in body
```

- [ ] **Step 2: Run; confirm failure**

Run: `pytest tests/test_app.py -v`
Expected: 404 on `/settings`.

- [ ] **Step 3: Create `its_briefing/templates/settings.html`**

```html
<!doctype html>
<html lang="en" class="h-full">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Settings — ITS Briefing</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    html, body { font-family: 'Inter', system-ui, sans-serif; }
    .mono { font-family: 'JetBrains Mono', monospace; }
  </style>
</head>
<body class="h-full bg-slate-900 text-slate-100">
  <div class="max-w-2xl mx-auto px-6 py-10">
    <header class="mb-8 border-b border-slate-800 pb-4 flex items-center justify-between">
      <h1 class="text-2xl font-bold text-cyan-400">Settings</h1>
      <a href="/" class="text-sm text-slate-400 hover:text-cyan-300">← Back to briefing</a>
    </header>

    {% if saved %}
      <div class="mb-6 p-3 rounded bg-emerald-900/40 border border-emerald-700 text-emerald-200 text-sm">
        Settings saved.
      </div>
    {% endif %}
    {% if error %}
      <div class="mb-6 p-3 rounded bg-red-900/40 border border-red-700 text-red-200 text-sm">
        {{ error }}
      </div>
    {% endif %}

    <form method="POST" action="/settings" class="space-y-6">
      <fieldset class="rounded-lg border border-slate-800 bg-slate-800/40 p-5">
        <legend class="px-2 text-sm uppercase tracking-wider text-slate-400">LLM Provider</legend>
        <div class="flex gap-6 mt-2">
          <label class="flex items-center gap-2">
            <input type="radio" name="llm_provider" value="ollama" {% if settings.llm_provider == 'ollama' %}checked{% endif %}>
            Ollama
          </label>
          <label class="flex items-center gap-2">
            <input type="radio" name="llm_provider" value="lmstudio" {% if settings.llm_provider == 'lmstudio' %}checked{% endif %}>
            LM Studio
          </label>
        </div>
      </fieldset>

      <fieldset class="rounded-lg border border-slate-800 bg-slate-800/40 p-5 space-y-4">
        <legend class="px-2 text-sm uppercase tracking-wider text-slate-400">Endpoint</legend>
        <label class="block">
          <span class="text-sm text-slate-400">Base URL</span>
          <input id="base_url" name="llm_base_url" type="text" value="{{ settings.llm_base_url }}"
                 class="mt-1 w-full rounded bg-slate-900 border border-slate-700 p-2 mono text-sm">
        </label>
        <label class="block">
          <span class="text-sm text-slate-400">Model</span>
          <input id="model" name="llm_model" type="text" value="{{ settings.llm_model }}"
                 class="mt-1 w-full rounded bg-slate-900 border border-slate-700 p-2 mono text-sm">
        </label>
        <div class="flex items-center gap-3">
          <button type="button" id="test-conn" class="px-3 py-1.5 rounded bg-slate-700 hover:bg-slate-600 text-sm">
            Test connection
          </button>
          <span id="test-result" class="text-sm"></span>
        </div>
      </fieldset>

      <fieldset class="rounded-lg border border-slate-800 bg-slate-800/40 p-5 space-y-4">
        <legend class="px-2 text-sm uppercase tracking-wider text-slate-400">Schedule</legend>
        <div class="grid grid-cols-3 gap-3">
          <label class="block">
            <span class="text-sm text-slate-400">Hour (0-23)</span>
            <input name="schedule_hour" type="number" min="0" max="23" value="{{ settings.schedule_hour }}"
                   class="mt-1 w-full rounded bg-slate-900 border border-slate-700 p-2 mono text-sm">
          </label>
          <label class="block">
            <span class="text-sm text-slate-400">Minute (0-59)</span>
            <input name="schedule_minute" type="number" min="0" max="59" value="{{ settings.schedule_minute }}"
                   class="mt-1 w-full rounded bg-slate-900 border border-slate-700 p-2 mono text-sm">
          </label>
          <label class="block">
            <span class="text-sm text-slate-400">Timezone</span>
            <input name="timezone" type="text" value="{{ settings.timezone }}"
                   class="mt-1 w-full rounded bg-slate-900 border border-slate-700 p-2 mono text-sm">
          </label>
        </div>
      </fieldset>

      <div class="flex gap-3">
        <button type="submit" class="px-4 py-2 rounded bg-cyan-500 text-slate-900 font-medium hover:bg-cyan-400">
          Save settings
        </button>
        <a href="/" class="px-4 py-2 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">Cancel</a>
      </div>
    </form>
  </div>

  <script>
    document.getElementById('test-conn').addEventListener('click', async () => {
      const provider = document.querySelector('input[name="llm_provider"]:checked').value;
      const base_url = document.getElementById('base_url').value;
      const model = document.getElementById('model').value;
      const out = document.getElementById('test-result');
      out.textContent = 'Testing…';
      out.className = 'text-sm text-slate-400';
      try {
        const r = await fetch('/api/test-connection', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({provider, base_url, model}),
        });
        const j = await r.json();
        if (j.ok) {
          out.textContent = `✓ ok (${j.models.length} models, ${j.latency_ms} ms)`;
          out.className = 'text-sm text-emerald-300';
        } else {
          out.textContent = `✗ ${j.error}`;
          out.className = 'text-sm text-red-300';
        }
      } catch (e) {
        out.textContent = `✗ ${e}`;
        out.className = 'text-sm text-red-300';
      }
    });
  </script>
</body>
</html>
```

- [ ] **Step 4: Add a gear icon link to `briefing.html`**

Edit `its_briefing/templates/briefing.html`. Find the header block (the `<header>` near the top with the title and date). Replace the header div with:

```html
    <header class="mb-10 border-b border-slate-800 pb-6">
      <div class="flex items-baseline justify-between flex-wrap gap-4">
        <h1 class="text-3xl font-bold text-cyan-400">ITS Briefing</h1>
        <div class="flex items-center gap-4">
          {% if briefing %}
            <div class="text-sm text-slate-400 mono">
              {{ briefing.date.isoformat() }} · generated {{ briefing.generated_at.strftime('%H:%M UTC') }}
            </div>
          {% endif %}
          <a href="/settings" title="Settings" class="text-slate-400 hover:text-cyan-300">⚙</a>
        </div>
      </div>
```

(Leave the rest of the header — the badge row — unchanged.)

- [ ] **Step 5: Add `GET /settings` route to `app.py`**

In `its_briefing/app.py`, add the route inside `create_app()` (alongside the existing routes):

```python
from flask import Flask, jsonify, render_template, request, redirect, url_for

from its_briefing import db, generate, scheduler, storage
from its_briefing.config import load_categories, load_sources

# ... inside create_app() ...

    @app.route("/settings", methods=["GET"])
    def settings_get():
        conn = db.get_connection()
        try:
            db.init_schema(conn)
            settings = db.get_settings(conn)
        finally:
            conn.close()
        saved = request.args.get("saved") == "1"
        return render_template("settings.html", settings=settings, saved=saved, error=None)
```

- [ ] **Step 6: Run; confirm pass**

Run: `pytest tests/test_app.py::test_get_settings_renders_current_values -v`
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add its_briefing/templates/settings.html its_briefing/templates/briefing.html its_briefing/app.py tests/test_app.py
git commit -m "feat(app): GET /settings page + gear link from briefing"
```

---

## Task 12: Add `POST /settings`

**Files:**
- Modify: `its_briefing/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_app.py`:

```python
def test_post_settings_updates_db_and_redirects(client):
    r = client.post(
        "/settings",
        data={
            "llm_provider": "ollama",
            "llm_base_url": "http://localhost:11434",
            "llm_model": "llama3.1:8b",
            "schedule_hour": "9",
            "schedule_minute": "0",
            "timezone": "Europe/Berlin",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "/settings" in r.headers["Location"]

    follow = client.get(r.headers["Location"])
    body = follow.get_data(as_text=True)
    assert "ollama" in body
    assert "llama3.1:8b" in body


def test_post_settings_rejects_bad_provider(client):
    r = client.post(
        "/settings",
        data={
            "llm_provider": "bogus",
            "llm_base_url": "http://x",
            "llm_model": "x",
            "schedule_hour": "0",
            "schedule_minute": "0",
            "timezone": "UTC",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "provider" in r.get_data(as_text=True).lower()


def test_post_settings_rejects_bad_timezone(client):
    r = client.post(
        "/settings",
        data={
            "llm_provider": "ollama",
            "llm_base_url": "http://x",
            "llm_model": "x",
            "schedule_hour": "0",
            "schedule_minute": "0",
            "timezone": "Not/AReal_Zone",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_post_settings_rejects_out_of_range_hour(client):
    r = client.post(
        "/settings",
        data={
            "llm_provider": "ollama",
            "llm_base_url": "http://x",
            "llm_model": "x",
            "schedule_hour": "99",
            "schedule_minute": "0",
            "timezone": "UTC",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run; confirm failure**

Run: `pytest tests/test_app.py -v`
Expected: 4 new tests fail (405 Method Not Allowed).

- [ ] **Step 3: Add `POST /settings` to `app.py`**

In `its_briefing/app.py`, inside `create_app()`:

```python
    @app.route("/settings", methods=["POST"])
    def settings_post():
        form = request.form
        provider = form.get("llm_provider", "")
        base_url = form.get("llm_base_url", "").strip()
        model = form.get("llm_model", "").strip()
        tz = form.get("timezone", "").strip()

        # ---- validation ----
        errors: list[str] = []
        if provider not in ("ollama", "lmstudio"):
            errors.append("provider must be 'ollama' or 'lmstudio'")
        if not base_url:
            errors.append("base_url is required")
        if not model:
            errors.append("model is required")
        try:
            hour = int(form.get("schedule_hour", ""))
            if not 0 <= hour <= 23:
                raise ValueError("hour out of range")
        except ValueError:
            errors.append("schedule_hour must be 0-23")
            hour = None
        try:
            minute = int(form.get("schedule_minute", ""))
            if not 0 <= minute <= 59:
                raise ValueError("minute out of range")
        except ValueError:
            errors.append("schedule_minute must be 0-59")
            minute = None

        # Validate timezone via APScheduler's CronTrigger (raises ValueError on bad tz).
        if not errors and tz:
            from apscheduler.triggers.cron import CronTrigger
            try:
                CronTrigger(hour=hour, minute=minute, timezone=tz)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"invalid timezone: {exc}")

        if errors:
            conn = db.get_connection()
            try:
                db.init_schema(conn)
                current = db.get_settings(conn)
            finally:
                conn.close()
            return (
                render_template(
                    "settings.html",
                    settings=current,
                    saved=False,
                    error="; ".join(errors),
                ),
                400,
            )

        # ---- save ----
        conn = db.get_connection()
        try:
            db.init_schema(conn)
            current = db.get_settings(conn)
            db.update_settings(
                conn,
                {
                    "llm_provider": provider,
                    "llm_base_url": base_url,
                    "llm_model": model,
                    "schedule_hour": hour,
                    "schedule_minute": minute,
                    "timezone": tz,
                },
            )
        finally:
            conn.close()

        # Reschedule if the cron-relevant fields changed.
        if (current.schedule_hour, current.schedule_minute, current.timezone) != (
            hour,
            minute,
            tz,
        ):
            try:
                scheduler.reschedule(hour, minute, tz)
            except RuntimeError:
                logger.info("Scheduler not running (likely under test); skipping reschedule")

        return redirect(url_for("settings_get") + "?saved=1")
```

- [ ] **Step 4: Run; confirm pass**

Run: `pytest tests/test_app.py -v`
Expected: all settings-related tests pass.

- [ ] **Step 5: Run full suite**

Run: `pytest`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add its_briefing/app.py tests/test_app.py
git commit -m "feat(app): POST /settings with validation and reschedule"
```

---

## Task 13: Add `POST /api/test-connection`

**Files:**
- Modify: `its_briefing/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_app.py`:

```python
from pytest_httpx import HTTPXMock


def test_test_connection_ollama_success(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://localhost:11434/api/tags",
        json={"models": [{"name": "llama3.1:8b"}, {"name": "mistral:7b"}]},
    )
    r = client.post(
        "/api/test-connection",
        json={"provider": "ollama", "base_url": "http://localhost:11434", "model": "llama3.1:8b"},
    )
    j = r.get_json()
    assert j["ok"] is True
    assert j["models"] == ["llama3.1:8b", "mistral:7b"]
    assert isinstance(j["latency_ms"], int)


def test_test_connection_lmstudio_success(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://localhost:1234/v1/models",
        json={"data": [{"id": "google/gemma-4-26b-a4b"}]},
    )
    r = client.post(
        "/api/test-connection",
        json={"provider": "lmstudio", "base_url": "http://localhost:1234", "model": "google/gemma-4-26b-a4b"},
    )
    j = r.get_json()
    assert j["ok"] is True
    assert j["models"] == ["google/gemma-4-26b-a4b"]


def test_test_connection_failure_returns_error_payload(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="http://localhost:11434/api/tags", status_code=500)
    r = client.post(
        "/api/test-connection",
        json={"provider": "ollama", "base_url": "http://localhost:11434", "model": "x"},
    )
    j = r.get_json()
    assert j["ok"] is False
    assert j["error"]
    assert j["models"] == []


def test_test_connection_rejects_bad_provider(client):
    r = client.post(
        "/api/test-connection",
        json={"provider": "bogus", "base_url": "http://x", "model": "y"},
    )
    j = r.get_json()
    assert j["ok"] is False
    assert "provider" in j["error"].lower()
```

- [ ] **Step 2: Run; confirm failure**

Run: `pytest tests/test_app.py -v`
Expected: 4 new tests fail (404).

- [ ] **Step 3: Add the route to `app.py`**

In `its_briefing/app.py`, inside `create_app()`:

```python
    @app.route("/api/test-connection", methods=["POST"])
    def test_connection():
        import time
        from its_briefing.llm import LLMClientError, LMStudioClient, OllamaClient

        body = request.get_json(silent=True) or {}
        provider = body.get("provider", "")
        base_url = (body.get("base_url") or "").strip()
        model = (body.get("model") or "").strip()

        if provider not in ("ollama", "lmstudio"):
            return jsonify({"ok": False, "models": [], "error": "provider must be 'ollama' or 'lmstudio'", "latency_ms": 0})
        if not base_url:
            return jsonify({"ok": False, "models": [], "error": "base_url is required", "latency_ms": 0})

        client = OllamaClient(base_url, model) if provider == "ollama" else LMStudioClient(base_url, model)
        start = time.perf_counter()
        try:
            models = client.list_models()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return jsonify({"ok": True, "models": models, "error": None, "latency_ms": latency_ms})
        except LLMClientError as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return jsonify({"ok": False, "models": [], "error": str(exc), "latency_ms": latency_ms})
```

- [ ] **Step 4: Run; confirm pass**

Run: `pytest tests/test_app.py -v`
Expected: all pass.

- [ ] **Step 5: Run full suite**

Run: `pytest`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add its_briefing/app.py tests/test_app.py
git commit -m "feat(app): POST /api/test-connection with no DB side effect"
```

---

## Task 14: Update `.env.example` and `CLAUDE.md`

**Files:**
- Modify: `.env.example`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rewrite `.env.example`**

Overwrite `.env.example`:

```env
# LLM provider — "ollama" or "lmstudio".
LLM_PROVIDER=ollama
# Endpoint for the chosen provider.
#   Ollama default:    http://localhost:11434
#   LM Studio default: http://localhost:1234
LLM_BASE_URL=http://localhost:11434
# Model name as the provider exposes it.
#   Ollama example:    llama3.1:8b
#   LM Studio example: google/gemma-4-26b-a4b
LLM_MODEL=llama3.1:8b

# Legacy aliases — still accepted as fallbacks for LLM_BASE_URL / LLM_MODEL on first boot.
# OLLAMA_BASE_URL=http://localhost:11434
# OLLAMA_MODEL=llama3.1:8b

TIMEZONE=Europe/Berlin
SCHEDULE_HOUR=6
SCHEDULE_MINUTE=0
FLASK_HOST=127.0.0.1
FLASK_PORT=8089
LOG_LEVEL=INFO

# Note: these env vars seed the SQLite DB at cache/its_briefing.db on first boot only.
# After that, the /settings page in the UI is the way to change provider/model/schedule.
# FLASK_HOST, FLASK_PORT and LOG_LEVEL remain process-bound (re-read from env on every start).
```

- [ ] **Step 2: Append a section to `CLAUDE.md`**

Add this section to `CLAUDE.md` after the "Configuration is the seam" subsection:

```markdown
### Settings live in SQLite

Runtime settings (LLM provider, base URL, model, schedule) live in `cache/its_briefing.db`. `.env` seeds the `settings` table the very first time the DB is created; after that, the `/settings` page in the UI is the source of truth. `FLASK_HOST`, `FLASK_PORT`, and `LOG_LEVEL` stay env-only because they bind at process start.

The DB also stores articles (with cross-day dedup via `id = sha256(link)[:16]`), briefings (one row per day plus a join table to articles), and a `generation_runs` log. Schema is defined in `db.py`'s `_SCHEMA_SQL` and applied idempotently via `init_schema()` on every startup. Schema version is tracked in `schema_version` for future migrations.

Two LLM clients live in `llm.py`: `OllamaClient` (POST /api/chat, reads `data["message"]["content"]`) and `LMStudioClient` (POST /v1/chat/completions, reads `data["choices"][0]["message"]["content"]`). `make_client(settings)` selects per `settings.llm_provider`. Adding a third provider means adding a class with `chat(prompt) -> str` and `list_models() -> list[str]`.
```

- [ ] **Step 3: Commit**

```bash
git add .env.example CLAUDE.md
git commit -m "docs: document LLM_* env vars and DB-as-truth settings model"
```

---

## Task 15: Manual verification

**Files:** none (verification only).

- [ ] **Step 1: Confirm no `cache/its_briefing.db` exists yet**

Run: `ls cache/`
Expected: maybe some old `briefing-*.json` files (ignored), but no `its_briefing.db`.

- [ ] **Step 2: Start the app**

Run: `python -m its_briefing`
Expected: `Scheduler started; next run at <date>` log line.
Leave running in another shell.

- [ ] **Step 3: DB file was created**

In another shell, run: `ls cache/its_briefing.db`
Expected: file exists.

- [ ] **Step 4: Open the briefing page**

Visit `http://127.0.0.1:8089/`.
Expected: page renders. Top-right shows a `⚙` icon next to the date.

- [ ] **Step 5: Open the settings page**

Click `⚙` (or visit `http://127.0.0.1:8089/settings`).
Expected: form is pre-filled with the values seeded from `.env` (provider, base URL, model, schedule).

- [ ] **Step 6: Test the LM Studio connection**

Set provider = LM Studio, base URL = `http://192.168.32.231:1234`, model = `google/gemma-4-26b-a4b`. Click **Test connection**.
Expected: ✓ ok with model count and latency, OR ✗ with a clear error message (network unreachable, etc).

- [ ] **Step 7: Save and rebuild**

Click **Save settings**. Then return to `/` and click **Rebuild now**.
Expected: pipeline runs without the `'message'` `KeyError`. After ~1-2 minutes (Ollama latency × ~50 articles), the page reloads with a new briefing showing classified articles.

- [ ] **Step 8: Confirm the run was logged**

Run: `python -c "import sqlite3; c = sqlite3.connect('cache/its_briefing.db'); c.row_factory = sqlite3.Row; print(dict(c.execute('SELECT * FROM generation_runs ORDER BY id DESC LIMIT 1').fetchone()))"`
Expected: a row with `succeeded=1`, non-null `finished_at`, and the article count.

- [ ] **Step 9: Switch back to Ollama and confirm**

Edit `/settings` back to provider = Ollama, base URL = `http://localhost:11434` (assuming a local Ollama is running with a pulled model). Save. Click Rebuild now.
Expected: pipeline runs through Ollama path.

- [ ] **Step 10: Verify schedule reschedule is persistent**

On the settings page, change the schedule hour to one that fires soon (e.g. current minute + 2). Save. Watch the logs.
Expected: a `Scheduler rescheduled to ...` log line, and the job actually fires at the new time.

- [ ] **Step 11: Final test run**

Run: `pytest`
Expected: all tests pass.

- [ ] **Step 12: Final commit (if anything was tweaked)**

If there were no changes during verification, skip. Otherwise commit fixes.

---

## Self-review notes

**Spec coverage check:**

- LLM provider abstraction → Task 6
- Bug fix (`'message'` KeyError) → Task 6 (LMStudioClient + dedicated regression test)
- DB schema (settings, articles, briefings, briefing_articles, generation_runs, schema_version) → Tasks 2, 3, 4, 5
- Settings precedence (env seeds once, DB is truth) → Tasks 3, 10
- Routes (GET /settings, POST /settings, POST /api/test-connection) → Tasks 11, 12, 13
- Scheduler reaction to schedule changes → Tasks 9, 12
- Storage public API stable → Task 7
- Generation runs log → Tasks 5, 8
- Test plan (unit + route + manual verification) → Tasks 1–13, 15
- `.env.example` + `CLAUDE.md` updates → Task 14

**Type consistency check:** `llm_provider` uses `"ollama"` / `"lmstudio"` literals throughout. `Settings` field names match between `config.py`, `db.py`, `app.py`, and templates. `make_client` is referenced in tests and implemented in Task 6. `LLMClientError` is exported and caught consistently. `db.DEFAULT_DB_PATH` is the single source of truth for the DB file path; tests monkeypatch it.

**No placeholders:** every step shows the exact code/command needed.
