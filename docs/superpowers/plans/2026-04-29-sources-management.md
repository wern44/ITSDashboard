# Sources Management & Per-Section Summarization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/sources` admin page with health checks, on-demand LLM diagnosis, and full source CRUD (DB-backed); fix the recurring "AI summary unavailable" placeholder by replacing the single summary prompt with per-category section summarization; surface the underlying LLM error on the briefing page.

**Architecture:** Sources move from `config/sources.yaml` to a SQLite `sources` table seeded once from the YAML on first init. A new `its_briefing/sources.py` module owns health checks (HTTP + feedparser) and on-demand LLM diagnosis. Health-check jobs run on the existing APScheduler thread pool, polled from the UI via JSON. `llm.build_summary()` is rewritten to call the LLM once per section, fed only the relevant categories. A new `briefings.last_error` column captures and surfaces the most recent per-section failure on the briefing page. The boundary rule from `CLAUDE.md` (`fetch`/`llm`/`storage`/`db`/`sources` are pure I/O; only `generate.run()` orchestrates) is preserved.

**Tech Stack:** Python 3.13, Flask, SQLite (stdlib), APScheduler, httpx, feedparser, Pydantic v2, Tailwind via CDN, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-04-29-sources-management-design.md`

---

## File Structure

**New:**
- `its_briefing/sources.py` — `health_check_one()`, `health_check_all()`, `diagnose_failure()`, in-memory job registry.
- `its_briefing/templates/sources.html` — table page, edit/add modal, analyze expander, polling JS.
- `tests/test_sources.py` — health-check + diagnosis tests.

**Modified:**
- `config/categories.yaml` — replace `IT News daily` with `Tech & Innovation`; add `Phishing`.
- `its_briefing/db.py` — `sources` table + CRUD + seed; `briefings.last_error` column; schema bumps v2 + v3.
- `its_briefing/config.py` — `Source` model gets `enabled` + health fields; `load_sources()` reads DB.
- `its_briefing/models.py` — `Briefing.last_error: Optional[str]`.
- `its_briefing/llm.py` — `build_summary` rewritten per-section; returns `tuple[ExecutiveSummary, Optional[str]]`.
- `its_briefing/generate.py` — uses enabled-only sources; back-feeds source statuses; persists `last_error` on briefing.
- `its_briefing/storage.py` — pass through `last_error`.
- `its_briefing/app.py` — new `/sources` route + JSON CRUD + check + diagnose endpoints; gear-menu dropdown.
- `its_briefing/templates/briefing.html` — gear dropdown nav; red error strip when `briefing.last_error`.
- `tests/test_db.py`, `tests/test_config.py`, `tests/test_generate.py`, `tests/test_llm.py`, `tests/test_app.py` — extended.

**Schema versions:**
- `v1` (current) → `v2`: create `sources` table, seed from YAML.
- `v2` → `v3`: `ALTER TABLE briefings ADD COLUMN last_error TEXT`.

---

## Task 1: Update categories.yaml

**Files:**
- Modify: `config/categories.yaml`

- [ ] **Step 1: Replace `IT News daily` with `Tech & Innovation` and add `Phishing`**

Overwrite `config/categories.yaml` with:

```yaml
categories:
  - name: "Tech & Innovation"
    description: "Notable IT/tech news: new technologies, major product or platform updates, significant industry announcements (not security-specific)"
    color: "#3b82f6"
  - name: "IT-Security"
    description: "Defensive security topics: best practices, frameworks, hardening, security operations"
    color: "#22d3ee"
  - name: "Cyber-Security"
    description: "Broader cyber news: industry, policy, geopolitics, nation-state activity"
    color: "#a78bfa"
  - name: "Phishing"
    description: "Active phishing campaigns, lures, smishing, BEC tactics, and social-engineering techniques currently in use"
    color: "#fb923c"
  - name: "Threats and Vulnerabilities"
    description: "CVEs, advisories, vulnerable software, patches"
    color: "#f59e0b"
  - name: "Hacks"
    description: "Confirmed breaches, ransomware incidents, data leaks"
    color: "#ef4444"
  - name: "0-Day"
    description: "Zero-day vulnerabilities and exploits actively used in the wild"
    color: "#dc2626"
  - name: "Regulation"
    description: "Compliance, NIS2, DORA, GDPR, government policy and law"
    color: "#10b981"
```

- [ ] **Step 2: Run config tests to confirm parser still loads**

Run: `pytest tests/test_config.py -v`
Expected: PASS (the existing `test_load_categories` validates structure, not exact names).

- [ ] **Step 3: Commit**

```bash
git add config/categories.yaml
git commit -m "feat(categories): add Phishing, replace IT News daily with Tech & Innovation"
```

---

## Task 2: Add `sources` table schema (v1 → v2)

**Files:**
- Modify: `its_briefing/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing test for v2 schema**

Append to `tests/test_db.py`:

```python
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
```

The existing `test_init_schema_is_idempotent` asserts `version == 1`. **Update that assertion in this same step** to `version >= 1` (or replace with the explicit current `SCHEMA_VERSION` constant) so it stays green across bumps.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_db.py -v`
Expected: FAIL on the three new tests (sources table missing, version still 1).

- [ ] **Step 3: Add the schema and migration**

In `its_briefing/db.py`, append to the `_SCHEMA_SQL` string the new table definition:

```python
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
```

Replace `init_schema` with a version-aware migrator:

```python
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS, including the v1→v2 migration test.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/db.py tests/test_db.py
git commit -m "feat(db): add sources table and schema v2 migration"
```

---

## Task 3: Sources CRUD in `db.py`

**Files:**
- Modify: `its_briefing/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests for CRUD**

Append to `tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_db.py -v -k source`
Expected: FAIL — names not importable.

- [ ] **Step 3: Implement CRUD**

Append to `its_briefing/db.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_db.py -v -k source`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/db.py tests/test_db.py
git commit -m "feat(db): sources CRUD + check-result recording"
```

---

## Task 4: Seed sources from YAML

**Files:**
- Modify: `its_briefing/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_db.py::test_seed_sources_from_yaml_inserts_when_empty -v`
Expected: FAIL — `seed_sources_from_yaml` not defined.

- [ ] **Step 3: Implement seeding**

Append to `its_briefing/db.py`:

```python
import yaml  # add at top of file if not already present


def seed_sources_from_yaml(conn: sqlite3.Connection, yaml_path: Path) -> None:
    """One-shot seed: insert YAML sources if and only if the table is empty."""
    count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    if count > 0:
        return
    if not yaml_path.exists():
        return
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    items = (data or {}).get("sources", [])
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            entry["name"],
            entry["url"],
            entry["lang"],
            1,  # enabled
            now,
            now,
        )
        for entry in items
    ]
    conn.executemany(
        """
        INSERT INTO sources (name, url, lang, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO NOTHING
        """,
        rows,
    )
    conn.commit()
```

- [ ] **Step 4: Wire seeding into `__main__.py`**

Modify `its_briefing/__main__.py` to call the seed after `init_schema`. Replace the `try` block in `main()`:

```python
    # First-boot DB init + settings + sources seed.
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.seed_settings_from_env(conn, env_settings)
        db.seed_sources_from_yaml(conn, config.DEFAULT_SOURCES_PATH)
        settings = db.get_settings(conn)
    finally:
        conn.close()
```

Add `from its_briefing import config` at the top of `__main__.py` if not already present.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add its_briefing/db.py its_briefing/__main__.py tests/test_db.py
git commit -m "feat(db): seed sources from YAML on first init"
```

---

## Task 5: Extend `Source` Pydantic model

**Files:**
- Modify: `its_briefing/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_config.py`:

```python
from datetime import datetime, timezone
from its_briefing.config import Source


def test_source_defaults_enabled_true():
    s = Source(name="A", url="https://a/", lang="EN")
    assert s.enabled is True
    assert s.last_status is None
    assert s.last_checked_at is None
    assert s.last_error is None
    assert s.last_diagnosis is None


def test_source_accepts_health_fields():
    s = Source(
        name="A",
        url="https://a/",
        lang="EN",
        enabled=False,
        last_status="failed",
        last_checked_at=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
        last_error="HTTP 503",
        last_diagnosis="Probably transient.",
    )
    assert s.enabled is False
    assert s.last_status == "failed"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_config.py -v -k source`
Expected: FAIL — extra fields not allowed by current model.

- [ ] **Step 3: Update the model**

In `its_briefing/config.py`, replace the `Source` class:

```python
from datetime import datetime
from typing import Optional


class Source(BaseModel):
    name: str
    url: str
    lang: str  # "EN" | "DE"
    enabled: bool = True
    last_status: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_diagnosis: Optional[str] = None
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/config.py tests/test_config.py
git commit -m "feat(config): Source gains enabled + health fields"
```

---

## Task 6: `config.load_sources()` reads from DB

**Files:**
- Modify: `its_briefing/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_config.py`:

```python
import pytest
from pathlib import Path

from its_briefing import config as config_module
from its_briefing.config import load_sources
from its_briefing.db import (
    create_source,
    get_connection,
    init_schema,
)


def test_load_sources_reads_from_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "t.db"
    monkeypatch.setattr("its_briefing.db.DEFAULT_DB_PATH", db_path)
    conn = get_connection(db_path)
    init_schema(conn)
    create_source(conn, name="A", url="https://a/", lang="EN", enabled=True)
    create_source(conn, name="B", url="https://b/", lang="DE", enabled=False)
    conn.close()

    result = load_sources()
    names = {s.name for s in result}
    assert names == {"A", "B"}


def test_load_sources_enabled_only_true(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "t.db"
    monkeypatch.setattr("its_briefing.db.DEFAULT_DB_PATH", db_path)
    conn = get_connection(db_path)
    init_schema(conn)
    create_source(conn, name="A", url="https://a/", lang="EN", enabled=True)
    create_source(conn, name="B", url="https://b/", lang="DE", enabled=False)
    conn.close()

    result = load_sources(enabled_only=True)
    names = {s.name for s in result}
    assert names == {"A"}


def test_load_sources_yaml_path_compat_shim(tmp_path: Path) -> None:
    """Passing a Path keeps the legacy YAML behaviour for tests."""
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  - name: X\n    url: https://x/\n    lang: EN\n",
        encoding="utf-8",
    )
    result = load_sources(yaml_path)
    assert [s.name for s in result] == ["X"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_config.py -v -k load_sources`
Expected: FAIL on the DB-backed cases.

- [ ] **Step 3: Rewrite `load_sources`**

In `its_briefing/config.py`, replace the existing `load_sources` with:

```python
def load_sources(
    path: Optional[Path] = None, *, enabled_only: bool = False
) -> list[Source]:
    """Return the configured sources.

    By default reads from the SQLite `sources` table (DB is the source of truth
    after first-boot seeding). For backwards compatibility with tests that pass
    an explicit YAML path, falls back to YAML parsing.
    """
    if path is not None:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return [Source(**entry) for entry in data["sources"]]

    # Local import to avoid circular import with its_briefing.db.
    from its_briefing import db as _db
    conn = _db.get_connection()
    try:
        _db.init_schema(conn)
        rows = _db.list_sources(conn, enabled_only=enabled_only)
    finally:
        conn.close()
    return [
        Source(
            name=r["name"],
            url=r["url"],
            lang=r["lang"],
            enabled=bool(r["enabled"]),
            last_status=r["last_status"],
            last_checked_at=(
                datetime.fromisoformat(r["last_checked_at"])
                if r["last_checked_at"]
                else None
            ),
            last_error=r["last_error"],
            last_diagnosis=r["last_diagnosis"],
        )
        for r in rows
    ]
```

Add `from typing import Optional` at the top if not already present.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/config.py tests/test_config.py
git commit -m "feat(config): load_sources reads from DB, YAML path is compat shim"
```

---

## Task 7: Pipeline uses enabled-only sources + back-feeds statuses

**Files:**
- Modify: `its_briefing/generate.py`
- Test: `tests/test_generate.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_generate.py`:

```python
from its_briefing.db import create_source, list_sources


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
```

Note: these tests assume `build_summary` returns `(ExecutiveSummary, Optional[str])` — that change lands in Task 11. Mark these tests with `pytest.mark.xfail(strict=False)` until Task 11, OR write Task 7's tests with a tuple-returning fake from the start (which is what the test code above already does). Existing `test_run_orchestrates_pipeline` etc. need their `build_summary` lambdas updated to return tuples in this same step:

```python
monkeypatch.setattr(
    generate.llm, "build_summary",
    lambda articles, settings, target_date: (ExecutiveSummary(), None),
)
```

Update every `build_summary` patch in `tests/test_generate.py` to return a tuple. **Do this in Step 1.**

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_generate.py -v`
Expected: FAIL on the new tests; existing tests should pass after the tuple update.

- [ ] **Step 3: Update `generate.run`**

In `its_briefing/generate.py`, replace the pipeline body (after `db.record_run_start(conn)` and the `finally`) with:

```python
        # --- pipeline ---
        sources = config.load_sources(enabled_only=True)
        categories = config.load_categories()

        logger.info("Fetching %d sources...", len(sources))
        articles, failed_sources = fetch.fetch_all(sources)
        logger.info(
            "Fetched %d articles, %d sources failed", len(articles), len(failed_sources)
        )

        # Back-feed health status into the sources table.
        try:
            status_conn = db.get_connection()
            try:
                # We need IDs to record results; map by name.
                rows = db.list_sources(status_conn)
                name_to_id = {r["name"]: r["id"] for r in rows}
                failed_set = set(failed_sources)
                for s in sources:
                    sid = name_to_id.get(s.name)
                    if sid is None:
                        continue
                    if s.name in failed_set:
                        db.record_source_check_result(
                            status_conn, sid, status="failed", error="fetch failed"
                        )
                    else:
                        db.record_source_check_result(
                            status_conn, sid, status="ok", error=None
                        )
            finally:
                status_conn.close()
        except Exception:  # noqa: BLE001 — never break the pipeline on status writes
            logger.exception("Failed to back-feed source statuses; continuing")

        for article in articles:
            article.category = llm.classify_article(article, categories, settings)

        now = datetime.now(timezone.utc)
        target_date = now.date()
        summary, summary_error = llm.build_summary(
            articles, settings, target_date=target_date
        )

        briefing = Briefing(
            date=target_date,
            generated_at=now,
            summary=summary,
            articles=articles,
            failed_sources=failed_sources,
            article_count=len(articles),
            last_error=summary_error,
        )

        storage.save_briefing(briefing)
```

The `last_error=summary_error` and `Briefing.last_error` field arrive in Tasks 8/11 — for now this will fail at construction. **Add a placeholder so this task lands in isolation:** drop the `last_error=summary_error` arg here and keep only the existing `Briefing(...)` fields. Add it back in Task 11. Same for the `summary, summary_error = llm.build_summary(...)` tuple unpack — temporarily call `summary = llm.build_summary(...)` until Task 10 is done. Mark this with a comment:

```python
        # NOTE: build_summary returns a tuple after Task 10 of the plan.
        summary = llm.build_summary(articles, settings, target_date=target_date)
        # ... and Briefing.last_error arrives in Task 11.
```

(So Task 7 only does the enabled-only filter and back-feed.)

Refresh the test in Step 1 to match: drop the tuple return on the `build_summary` fake **for tests added in Task 7 only**:

```python
monkeypatch.setattr(
    generate.llm, "build_summary",
    lambda articles, settings, target_date: ExecutiveSummary(),
)
```

The tuple-returning fakes will be re-introduced in Task 11.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_generate.py -v`
Expected: PASS, including the two new tests.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/generate.py tests/test_generate.py
git commit -m "feat(generate): use enabled-only sources and back-feed statuses"
```

---

## Task 8: Add `briefings.last_error` column (schema v2 → v3)

**Files:**
- Modify: `its_briefing/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db.py`:

```python
def test_briefings_last_error_column_exists(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(briefings)").fetchall()}
    assert "last_error" in cols
    conn.close()


def test_init_schema_bumps_to_v3(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version >= 3
    conn.close()


def test_v2_to_v3_migration_adds_last_error(tmp_path: Path) -> None:
    db_path = tmp_path / "v2.db"
    conn = get_connection(db_path)
    # Build a synthetic v2 (no last_error column).
    conn.executescript("""
        CREATE TABLE briefings (
            date TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            failed_sources TEXT NOT NULL
        );
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        INSERT INTO schema_version (version) VALUES (2);
    """)
    conn.commit()
    conn.close()

    conn = get_connection(db_path)
    init_schema(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(briefings)").fetchall()}
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()
    assert "last_error" in cols
    assert version == 3
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_db.py -v -k last_error`
Expected: FAIL.

- [ ] **Step 3: Update schema + migration logic**

In `its_briefing/db.py`:

1. Bump `SCHEMA_VERSION = 3`.
2. The `_SCHEMA_SQL` runs `CREATE TABLE IF NOT EXISTS briefings` — for fresh DBs, add `last_error TEXT` to that CREATE statement:

```python
CREATE TABLE IF NOT EXISTS briefings (
    date           TEXT PRIMARY KEY,
    generated_at   TEXT NOT NULL,
    summary_json   TEXT NOT NULL,
    failed_sources TEXT NOT NULL,
    last_error     TEXT
);
```

3. For existing DBs at v2, an explicit `ALTER TABLE` is needed since `CREATE TABLE IF NOT EXISTS` is a no-op when the table exists. Replace `init_schema` with:

```python
def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation + forward migrations."""
    conn.executescript(_SCHEMA_SQL)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    current = row["version"] if row else 0
    if current == 0:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    else:
        if current < 3:
            # v2 → v3: add briefings.last_error if missing.
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(briefings)").fetchall()}
            if "last_error" not in cols:
                conn.execute("ALTER TABLE briefings ADD COLUMN last_error TEXT")
        if current < SCHEMA_VERSION:
            conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    conn.commit()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/db.py tests/test_db.py
git commit -m "feat(db): add briefings.last_error (schema v3)"
```

---

## Task 9: Plumb `last_error` through models + storage + db CRUD

**Files:**
- Modify: `its_briefing/models.py`, `its_briefing/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db.py`:

```python
def test_save_and_load_briefing_round_trips_last_error(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    briefing = Briefing(
        date=date(2026, 4, 29),
        generated_at=datetime(2026, 4, 29, 6, 0, tzinfo=timezone.utc),
        summary=ExecutiveSummary(critical_vulnerabilities=[Bullet(text="hi")]),
        articles=[],
        failed_sources=[],
        article_count=0,
        last_error="HTTP 400 from /v1/chat/completions: n_keep > n_ctx",
    )
    db_save_briefing(conn, briefing)
    loaded = db_latest_briefing(conn)
    assert loaded is not None
    assert loaded.last_error == briefing.last_error
    conn.close()


def test_load_briefing_last_error_defaults_to_none(tmp_path: Path) -> None:
    conn = get_connection(tmp_path / "t.db")
    init_schema(conn)
    briefing = Briefing(
        date=date(2026, 4, 28),
        generated_at=datetime(2026, 4, 28, 6, 0, tzinfo=timezone.utc),
        summary=ExecutiveSummary(),
        articles=[],
        failed_sources=[],
        article_count=0,
    )
    db_save_briefing(conn, briefing)
    loaded = db_latest_briefing(conn)
    assert loaded.last_error is None
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_db.py -v -k last_error`
Expected: FAIL — model lacks the field; SQL doesn't write it.

- [ ] **Step 3: Update `Briefing` model**

In `its_briefing/models.py`, modify the `Briefing` class:

```python
class Briefing(BaseModel):
    """A complete daily briefing."""

    date: date
    generated_at: datetime  # UTC
    summary: ExecutiveSummary
    articles: list[Article]
    failed_sources: list[str] = Field(default_factory=list)
    article_count: int
    last_error: Optional[str] = None
```

- [ ] **Step 4: Update `save_briefing` and `load_briefing` in `db.py`**

In `its_briefing/db.py`, modify `save_briefing`:

```python
        conn.execute(
            """
            INSERT INTO briefings (date, generated_at, summary_json, failed_sources, last_error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                generated_at = excluded.generated_at,
                summary_json = excluded.summary_json,
                failed_sources = excluded.failed_sources,
                last_error = excluded.last_error
            """,
            (
                briefing.date.isoformat(),
                briefing.generated_at.isoformat(),
                briefing.summary.model_dump_json(),
                json.dumps(briefing.failed_sources),
                briefing.last_error,
            ),
        )
```

And `load_briefing`:

```python
def load_briefing(
    conn: sqlite3.Connection, target_date: date_type
) -> Optional[Briefing]:
    row = conn.execute(
        "SELECT date, generated_at, summary_json, failed_sources, last_error "
        "FROM briefings WHERE date = ?",
        (target_date.isoformat(),),
    ).fetchone()
    if row is None:
        return None
    # ... existing article load unchanged ...
    return Briefing(
        date=target_date,
        generated_at=datetime.fromisoformat(row["generated_at"]),
        summary=ExecutiveSummary.model_validate_json(row["summary_json"]),
        articles=articles,
        failed_sources=json.loads(row["failed_sources"]),
        article_count=len(articles),
        last_error=row["last_error"],
    )
```

(Keep the article-loading block in the middle unchanged; only the SELECT, the comment, and the return need updating.)

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_db.py tests/test_storage.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add its_briefing/models.py its_briefing/db.py tests/test_db.py
git commit -m "feat(models): Briefing.last_error round-trips through SQLite"
```

---

## Task 10: Per-section `build_summary`

**Files:**
- Modify: `its_briefing/llm.py`
- Test: `tests/test_llm.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_llm.py`:

```python
from its_briefing.llm import SECTION_CATEGORIES, build_summary


def _all_categories() -> list[Category]:
    return [
        Category(name="Tech & Innovation", description="..."),
        Category(name="IT-Security", description="..."),
        Category(name="Cyber-Security", description="..."),
        Category(name="Phishing", description="..."),
        Category(name="Threats and Vulnerabilities", description="..."),
        Category(name="Hacks", description="..."),
        Category(name="0-Day", description="..."),
        Category(name="Regulation", description="..."),
    ]


def _article_with_category(id_: str, category: str) -> Article:
    return Article(
        id=id_,
        source="Test",
        source_lang="EN",
        title=f"Article {id_}",
        link=f"https://example.com/{id_}",
        published=datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc),
        summary="text",
        category=category,
    )


def test_section_categories_mapping_includes_phishing():
    assert "Phishing" in SECTION_CATEGORIES["active_threats"]


def test_build_summary_returns_tuple(httpx_mock: HTTPXMock):
    settings = _settings("ollama", "http://localhost:11434")
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": '{"bullets":[]}'}},
    )
    result = build_summary([], settings, target_date=date(2026, 4, 29))
    assert isinstance(result, tuple)
    summary, error = result
    assert isinstance(summary, ExecutiveSummary)


def test_build_summary_empty_section_skips_llm_call(httpx_mock: HTTPXMock):
    """No articles in any mapped category → no LLM calls; placeholder NOT emitted."""
    settings = _settings("ollama", "http://localhost:11434")
    # Only Tech & Innovation articles (not in any section mapping).
    articles = [_article_with_category("a1", "Tech & Innovation")]
    summary, error = build_summary(articles, settings, target_date=date(2026, 4, 29))
    assert summary.critical_vulnerabilities == []
    assert summary.active_threats == []
    assert summary.notable_incidents == []
    assert summary.strategic_policy == []
    assert error is None
    # No HTTP requests should have been made.
    assert len(httpx_mock.get_requests()) == 0


def test_build_summary_per_section_only_passes_relevant_articles(httpx_mock: HTTPXMock):
    settings = _settings("ollama", "http://localhost:11434")
    articles = [
        _article_with_category("a1", "0-Day"),
        _article_with_category("a2", "Hacks"),
        _article_with_category("a3", "Regulation"),
        _article_with_category("a4", "Tech & Innovation"),
    ]

    # Capture each request body.
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": '{"bullets":[]}'}},
        is_reusable=True,
    )

    summary, error = build_summary(articles, settings, target_date=date(2026, 4, 29))
    requests = httpx_mock.get_requests()
    assert len(requests) >= 3  # at least crit_vuln, active_threats, notable_incidents, strategic_policy
    # Tech & Innovation article (a4) must NOT appear in any prompt.
    for req in requests:
        body = req.read().decode()
        assert "a4" not in body


def test_build_summary_one_section_failure_does_not_break_others(httpx_mock: HTTPXMock):
    settings = _settings("ollama", "http://localhost:11434")
    articles = [
        _article_with_category("a1", "0-Day"),
        _article_with_category("a2", "Hacks"),
    ]
    # First two requests OK, all subsequent ones fail.
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": '{"bullets":[{"text":"ok bullet","article_ids":["a1"]}]}'}},
    )
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        status_code=500,
        is_reusable=True,
    )
    summary, error = build_summary(articles, settings, target_date=date(2026, 4, 29))
    # First section produced a bullet; later sections empty; error captured.
    assert summary.critical_vulnerabilities  # at least one bullet
    assert error is not None and "500" in error


def test_build_summary_all_sections_failed_returns_placeholder(httpx_mock: HTTPXMock):
    settings = _settings("ollama", "http://localhost:11434")
    articles = [_article_with_category("a1", "0-Day")]
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        status_code=400,
        text="bad",
        is_reusable=True,
    )
    summary, error = build_summary(articles, settings, target_date=date(2026, 4, 29))
    # Placeholder bullet present.
    assert summary.critical_vulnerabilities
    assert "AI summary unavailable" in summary.critical_vulnerabilities[0].text
    assert error is not None
```

(Imports at top of file: `from datetime import date, datetime, timezone` should already be present; ensure `from its_briefing.llm import build_summary` and `SECTION_CATEGORIES`.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_llm.py -v -k summary`
Expected: FAIL — `SECTION_CATEGORIES` undefined; `build_summary` returns `ExecutiveSummary` not tuple.

- [ ] **Step 3: Rewrite `build_summary`**

In `its_briefing/llm.py`, replace `_summary_prompt`, `_try_build_summary`, and `build_summary` with:

```python
SECTION_CATEGORIES: dict[str, list[str]] = {
    "critical_vulnerabilities": ["Threats and Vulnerabilities", "0-Day"],
    "active_threats":           ["0-Day", "Hacks", "Phishing"],
    "notable_incidents":        ["Hacks"],
    "strategic_policy":         ["Regulation", "Cyber-Security", "IT-Security"],
}

_SECTION_DESCRIPTIONS: dict[str, str] = {
    "critical_vulnerabilities": "CVEs, advisories, urgent patches",
    "active_threats":           "ongoing campaigns, malware, phishing, threat actor activity",
    "notable_incidents":        "confirmed breaches, ransomware victims, data leaks",
    "strategic_policy":         "regulation, geopolitics, industry trends",
}


def _section_prompt(section: str, articles: list[Article]) -> str:
    article_lines = []
    for a in articles:
        snippet = a.summary[:300].replace("\n", " ")
        article_lines.append(f"[{a.id}] {a.title} — {snippet}")
    article_block = "\n".join(article_lines)
    description = _SECTION_DESCRIPTIONS[section]
    return (
        "You are a cybersecurity briefing analyst. Read the articles below and produce "
        f"a list of bullets for the section: {section} ({description}).\n\n"
        "Each bullet has a short text (1-2 sentences) and a list of article_ids that "
        "support it. Use the bracketed [id] from each article line. An empty list is "
        "allowed.\n\n"
        f"Articles:\n{article_block}\n\n"
        'Respond with JSON only: {"bullets":[{"text":"...","article_ids":["..."]}]}'
    )


def _try_build_section(
    section: str, articles: list[Article], settings: Settings
) -> list[Bullet]:
    client = make_client(settings)
    content = client.chat(_section_prompt(section, articles))
    parsed = json.loads(_strip_code_fences(content))
    bullets_raw = parsed.get("bullets", [])
    return [Bullet.model_validate(b) for b in bullets_raw]


def build_summary(
    articles: list[Article], settings: Settings, target_date: date
) -> tuple[ExecutiveSummary, Optional[str]]:
    """Per-section summarization. Returns (summary, last_error_message_or_None)."""
    section_results: dict[str, list[Bullet]] = {k: [] for k in SECTION_CATEGORIES}
    last_error: Optional[str] = None
    section_failed: dict[str, bool] = {k: False for k in SECTION_CATEGORIES}

    for section, allowed in SECTION_CATEGORIES.items():
        subset = [a for a in articles if a.category in allowed]
        if not subset:
            continue
        success = False
        for attempt in (1, 2):
            try:
                section_results[section] = _try_build_section(section, subset, settings)
                success = True
                break
            except (LLMClientError, json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
                logger.warning("Section %s attempt %d failed: %s", section, attempt, exc)
                last_error = str(exc)
        if not success:
            section_failed[section] = True

    # If every populated section failed, return the placeholder.
    populated = [s for s, allowed in SECTION_CATEGORIES.items()
                 if any(a.category in allowed for a in articles)]
    if populated and all(section_failed[s] for s in populated):
        return ExecutiveSummary.placeholder(target_date), last_error

    return (
        ExecutiveSummary(
            critical_vulnerabilities=section_results["critical_vulnerabilities"],
            active_threats=section_results["active_threats"],
            notable_incidents=section_results["notable_incidents"],
            strategic_policy=section_results["strategic_policy"],
        ),
        last_error,
    )
```

Add `from typing import Optional` at the top if not already present.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_llm.py -v`
Expected: PASS.

- [ ] **Step 5: Update other tests that mock build_summary**

In `tests/test_generate.py`, change every `build_summary` patch to return a tuple:

```python
monkeypatch.setattr(
    generate.llm, "build_summary",
    lambda articles, settings, target_date: (ExecutiveSummary(), None),
)
```

Run: `pytest tests/ -v`
Expected: All green.

- [ ] **Step 6: Commit**

```bash
git add its_briefing/llm.py tests/test_llm.py tests/test_generate.py
git commit -m "feat(llm): per-section build_summary, returns (summary, error) tuple"
```

---

## Task 11: Wire `last_error` through `generate.run()`

**Files:**
- Modify: `its_briefing/generate.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_generate.py`:

```python
def test_run_persists_summary_error(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    _seed_db(db_path)
    _patch_db_paths(monkeypatch, db_path)

    monkeypatch.setattr(generate.fetch, "fetch_all", lambda sources: ([_make_article("a1")], []))
    monkeypatch.setattr(generate.llm, "classify_article", lambda *a, **k: "0-Day")
    monkeypatch.setattr(
        generate.llm, "build_summary",
        lambda articles, settings, target_date: (ExecutiveSummary(), "HTTP 400: n_keep>n_ctx"),
    )

    briefing = generate.run()
    assert briefing is not None
    assert briefing.last_error == "HTTP 400: n_keep>n_ctx"

    # And it persists.
    from its_briefing import storage
    loaded = storage.latest_briefing()
    assert loaded.last_error == "HTTP 400: n_keep>n_ctx"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_generate.py::test_run_persists_summary_error -v`
Expected: FAIL — `Briefing` constructed without `last_error`.

- [ ] **Step 3: Update `generate.run`**

In `its_briefing/generate.py`, update the summary block and Briefing construction to:

```python
        now = datetime.now(timezone.utc)
        target_date = now.date()
        summary, summary_error = llm.build_summary(
            articles, settings, target_date=target_date
        )

        briefing = Briefing(
            date=target_date,
            generated_at=now,
            summary=summary,
            articles=articles,
            failed_sources=failed_sources,
            article_count=len(articles),
            last_error=summary_error,
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/generate.py tests/test_generate.py
git commit -m "feat(generate): persist summary last_error on the briefing"
```

---

## Task 12: `sources.health_check_one`

**Files:**
- Create: `its_briefing/sources.py`
- Test: `tests/test_sources.py`

- [ ] **Step 1: Create test file**

Create `tests/test_sources.py`:

```python
"""Tests for its_briefing.sources."""
from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from its_briefing.config import Source
from its_briefing.sources import HealthResult, health_check_one


def _src(url: str = "https://example.com/feed") -> Source:
    return Source(name="Test", url=url, lang="EN")


def _valid_atom() -> bytes:
    return b"""<?xml version='1.0'?>
<feed xmlns='http://www.w3.org/2005/Atom'>
  <title>T</title>
  <entry>
    <title>e</title>
    <link href='https://example.com/a'/>
    <id>1</id>
    <updated>2026-04-29T10:00:00Z</updated>
  </entry>
</feed>"""


def test_health_check_one_ok_with_entries(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://example.com/feed", content=_valid_atom())
    result = health_check_one(_src())
    assert isinstance(result, HealthResult)
    assert result.status == "ok"
    assert result.error is None


def test_health_check_one_failed_on_5xx(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://example.com/feed", status_code=503)
    result = health_check_one(_src())
    assert result.status == "failed"
    assert "503" in result.error


def test_health_check_one_failed_on_zero_entries(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://example.com/feed",
        content=b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><title>T</title></feed>",
    )
    result = health_check_one(_src())
    assert result.status == "failed"
    assert "no entries" in result.error.lower()


def test_health_check_one_failed_on_timeout(httpx_mock: HTTPXMock):
    import httpx
    httpx_mock.add_exception(httpx.ConnectTimeout("timeout"), url="https://example.com/feed")
    result = health_check_one(_src())
    assert result.status == "failed"
    assert "connection" in result.error.lower() or "timeout" in result.error.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_sources.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create `its_briefing/sources.py`**

```python
"""Source health checks and on-demand LLM diagnosis."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import feedparser
import httpx

from its_briefing.config import Source

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 10


@dataclass
class HealthResult:
    status: str  # "ok" | "failed"
    error: Optional[str]


def health_check_one(source: Source) -> HealthResult:
    """GET the feed URL and parse it. Status mirrors fetch._fetch_one semantics."""
    try:
        response = httpx.get(
            source.url,
            timeout=CHECK_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": "ITS-Briefing/0.1"},
        )
    except httpx.TimeoutException:
        return HealthResult("failed", "connection: timeout")
    except httpx.ConnectError as exc:
        return HealthResult("failed", f"connection: {exc.__class__.__name__}")
    except httpx.HTTPError as exc:
        return HealthResult("failed", f"connection: {exc.__class__.__name__}")

    if not response.is_success:
        return HealthResult("failed", f"HTTP {response.status_code}")

    try:
        parsed = feedparser.parse(response.content)
    except Exception as exc:  # noqa: BLE001
        return HealthResult("failed", f"parse: {exc.__class__.__name__}")

    if not parsed.entries:
        bozo_msg = ""
        if getattr(parsed, "bozo", False):
            be = getattr(parsed, "bozo_exception", None)
            bozo_msg = f": {be.__class__.__name__}" if be is not None else ""
        return HealthResult("failed", f"parse: no entries{bozo_msg}")

    return HealthResult("ok", None)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sources.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/sources.py tests/test_sources.py
git commit -m "feat(sources): health_check_one"
```

---

## Task 13: `health_check_all` + job registry

**Files:**
- Modify: `its_briefing/sources.py`
- Test: `tests/test_sources.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_sources.py`:

```python
import time
from pathlib import Path

from its_briefing.db import create_source, get_connection, init_schema, list_sources
from its_briefing.sources import (
    get_check_job,
    start_health_check_job,
)


def test_start_health_check_job_returns_job_id(tmp_path: Path, monkeypatch, httpx_mock: HTTPXMock) -> None:
    db_path = tmp_path / "t.db"
    monkeypatch.setattr("its_briefing.db.DEFAULT_DB_PATH", db_path)
    conn = get_connection(db_path)
    init_schema(conn)
    create_source(conn, name="Good", url="https://good/", lang="EN", enabled=True)
    conn.close()

    httpx_mock.add_response(url="https://good/", content=_valid_atom())

    job_id = start_health_check_job(source_ids=None)  # None = all
    assert isinstance(job_id, str) and len(job_id) >= 8

    # Wait briefly for the background thread.
    for _ in range(50):
        job = get_check_job(job_id)
        if job["state"] == "done":
            break
        time.sleep(0.05)
    job = get_check_job(job_id)
    assert job["state"] == "done"
    assert "Good" in {r["name"] for r in list_sources(get_connection(db_path))}


def test_health_check_persists_results(tmp_path: Path, monkeypatch, httpx_mock: HTTPXMock) -> None:
    db_path = tmp_path / "t.db"
    monkeypatch.setattr("its_briefing.db.DEFAULT_DB_PATH", db_path)
    conn = get_connection(db_path)
    init_schema(conn)
    sid = create_source(conn, name="Good", url="https://good/", lang="EN", enabled=True)
    conn.close()

    httpx_mock.add_response(url="https://good/", content=_valid_atom())
    job_id = start_health_check_job(source_ids=[sid])
    for _ in range(50):
        if get_check_job(job_id)["state"] == "done":
            break
        time.sleep(0.05)
    conn = get_connection(db_path)
    row = conn.execute("SELECT last_status, last_error FROM sources WHERE id = ?", (sid,)).fetchone()
    conn.close()
    assert row["last_status"] == "ok"
    assert row["last_error"] is None


def test_get_check_job_returns_none_for_unknown_id() -> None:
    assert get_check_job("does-not-exist") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_sources.py -v -k health_check_job`
Expected: FAIL — `start_health_check_job` undefined.

- [ ] **Step 3: Implement job registry + `health_check_all`**

Append to `its_briefing/sources.py`:

```python
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Iterable, Optional

from its_briefing import db as _db


_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_MAX_WORKERS = 10


def start_health_check_job(source_ids: Optional[Iterable[int]] = None) -> str:
    """Kick off a background health-check job. Returns the job_id."""
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "state": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source_ids": list(source_ids) if source_ids is not None else None,
            "results": {},
        }
    thread = threading.Thread(
        target=_run_job, args=(job_id,), name=f"healthcheck-{job_id[:6]}", daemon=True
    )
    thread.start()
    return job_id


def get_check_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None


def _load_sources_for_job(source_ids: Optional[list[int]]) -> list[tuple[int, Source]]:
    conn = _db.get_connection()
    try:
        _db.init_schema(conn)
        rows = _db.list_sources(conn)
    finally:
        conn.close()
    pairs: list[tuple[int, Source]] = []
    for r in rows:
        if source_ids is not None and r["id"] not in source_ids:
            continue
        pairs.append((
            r["id"],
            Source(name=r["name"], url=r["url"], lang=r["lang"], enabled=bool(r["enabled"])),
        ))
    return pairs


def _mark_checking(source_id: int) -> None:
    conn = _db.get_connection()
    try:
        _db.update_source(conn, source_id, {"last_status": "checking"})
    finally:
        conn.close()


def _persist_result(source_id: int, result: HealthResult) -> None:
    conn = _db.get_connection()
    try:
        _db.record_source_check_result(conn, source_id, status=result.status, error=result.error)
    finally:
        conn.close()


def _run_job(job_id: str) -> None:
    try:
        with _jobs_lock:
            source_ids = _jobs[job_id]["source_ids"]
        pairs = _load_sources_for_job(source_ids)
        for sid, _src in pairs:
            _mark_checking(sid)
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futures = {ex.submit(health_check_one, src): sid for sid, src in pairs}
            for future in futures:
                sid = futures[future]
                result = future.result()
                _persist_result(sid, result)
                with _jobs_lock:
                    _jobs[job_id]["results"][sid] = {"status": result.status, "error": result.error}
    except Exception as exc:  # noqa: BLE001
        logger.exception("health-check job %s crashed", job_id)
        with _jobs_lock:
            _jobs[job_id]["error"] = str(exc)
    finally:
        with _jobs_lock:
            _jobs[job_id]["state"] = "done"
```

Note: importing `Source` is already done at the top in Task 12. The new imports (`threading`, `uuid`, `ThreadPoolExecutor`, `datetime`, `Iterable`, `Optional`, `_db`) need to be added at the top of `sources.py`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sources.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/sources.py tests/test_sources.py
git commit -m "feat(sources): background health-check jobs with status registry"
```

---

## Task 14: `diagnose_failure`

**Files:**
- Modify: `its_briefing/sources.py`
- Test: `tests/test_sources.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_sources.py`:

```python
from its_briefing.sources import diagnose_failure
from its_briefing.config import Settings


def _settings() -> Settings:
    return Settings(
        llm_provider="ollama",
        llm_base_url="http://localhost:11434",
        llm_model="x",
        timezone="UTC",
        schedule_hour=6,
        schedule_minute=0,
        flask_host="127.0.0.1",
        flask_port=8089,
        log_level="INFO",
    )


def test_diagnose_failure_returns_suggestion(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": '{"likely_cause":"throttling","suggested_fix":"retry"}'}},
    )
    suggestion, error = diagnose_failure(
        source_name="X", url="https://x/", last_error="HTTP 503", settings=_settings()
    )
    assert error is None
    assert "throttling" in suggestion
    assert "retry" in suggestion


def test_diagnose_failure_returns_error_when_llm_fails(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat", status_code=500, text="boom"
    )
    suggestion, error = diagnose_failure(
        source_name="X", url="https://x/", last_error="HTTP 503", settings=_settings()
    )
    assert suggestion is None
    assert error is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_sources.py -v -k diagnose`
Expected: FAIL.

- [ ] **Step 3: Implement diagnose**

Append to `its_briefing/sources.py`:

```python
import json
from its_briefing.config import Settings
from its_briefing.llm import LLMClientError, _strip_code_fences, make_client


def diagnose_failure(
    *,
    source_name: str,
    url: str,
    last_error: str,
    settings: Settings,
) -> tuple[Optional[str], Optional[str]]:
    """Ask the LLM to diagnose a feed failure. Returns (suggestion, error_or_None)."""
    prompt = (
        "A cybersecurity-news RSS feed is failing. Diagnose it briefly.\n\n"
        f"Source name: {source_name}\n"
        f"URL: {url}\n"
        f"Last error: {last_error}\n\n"
        'Respond as JSON: {"likely_cause": "...", "suggested_fix": "..."}\n'
        "Be concise: one sentence per field."
    )
    try:
        client = make_client(settings)
        content = client.chat(prompt)
        parsed = json.loads(_strip_code_fences(content))
        cause = parsed.get("likely_cause", "").strip()
        fix = parsed.get("suggested_fix", "").strip()
        if not cause and not fix:
            return None, "LLM returned empty diagnosis"
        return f"{cause} — {fix}", None
    except (LLMClientError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return None, str(exc)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sources.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/sources.py tests/test_sources.py
git commit -m "feat(sources): diagnose_failure"
```

---

## Task 15: `/sources` page route + JSON listing

**Files:**
- Modify: `its_briefing/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_app.py` (use whatever helpers it already has for an in-memory DB):

```python
def test_get_sources_page(client, seed_db):
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert b"Sources" in resp.data


def test_get_api_sources_returns_list(client, seed_db):
    # seed at least one source
    from its_briefing import db
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.create_source(conn, name="Z", url="https://z/", lang="EN", enabled=True)
    finally:
        conn.close()
    resp = client.get("/api/sources")
    assert resp.status_code == 200
    items = resp.get_json()["sources"]
    assert any(s["name"] == "Z" for s in items)
```

If `tests/test_app.py` uses different fixtures (read it first), adapt these tests to its existing pattern. The exact fixtures are already established in the file — match them.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_app.py -v -k sources`
Expected: FAIL — routes don't exist.

- [ ] **Step 3: Add the routes**

In `its_briefing/app.py`, add to `create_app()` after the `/settings` routes:

```python
    @app.route("/sources", methods=["GET"])
    def sources_page():
        from its_briefing import db as _db
        conn = _db.get_connection()
        try:
            _db.init_schema(conn)
            rows = _db.list_sources(conn)
        finally:
            conn.close()
        return render_template("sources.html", sources=rows)

    @app.route("/api/sources", methods=["GET"])
    def api_sources_list():
        from its_briefing import db as _db
        conn = _db.get_connection()
        try:
            _db.init_schema(conn)
            rows = _db.list_sources(conn)
        finally:
            conn.close()
        items = [
            {
                "id": r["id"],
                "name": r["name"],
                "url": r["url"],
                "lang": r["lang"],
                "enabled": bool(r["enabled"]),
                "last_status": r["last_status"],
                "last_checked_at": r["last_checked_at"],
                "last_error": r["last_error"],
                "last_diagnosis": r["last_diagnosis"],
            }
            for r in rows
        ]
        return jsonify({"sources": items})
```

The template will be created in Task 19; for now create a stub at `its_briefing/templates/sources.html`:

```html
<!doctype html>
<html><head><title>Sources</title></head>
<body><h1>Sources</h1><ul>
{% for s in sources %}<li>{{ s["name"] }}</li>{% endfor %}
</ul></body></html>
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_app.py -v -k sources`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/app.py its_briefing/templates/sources.html tests/test_app.py
git commit -m "feat(app): /sources page + GET /api/sources"
```

---

## Task 16: Source CRUD JSON endpoints

**Files:**
- Modify: `its_briefing/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_app.py`:

```python
def test_post_api_sources_creates(client, seed_db):
    resp = client.post(
        "/api/sources",
        json={"name": "New", "url": "https://n/", "lang": "EN", "enabled": True},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["source"]["name"] == "New"
    assert body["source"]["id"] >= 1


def test_post_api_sources_validation_errors(client, seed_db):
    resp = client.post("/api/sources", json={"name": "", "url": "", "lang": "FR"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["errors"]


def test_post_api_sources_duplicate_name(client, seed_db):
    client.post("/api/sources", json={"name": "Dup", "url": "https://a/", "lang": "EN"})
    resp = client.post("/api/sources", json={"name": "Dup", "url": "https://b/", "lang": "EN"})
    assert resp.status_code == 400


def test_patch_api_sources(client, seed_db):
    resp = client.post("/api/sources", json={"name": "X", "url": "https://x/", "lang": "EN"})
    sid = resp.get_json()["source"]["id"]
    resp = client.patch(f"/api/sources/{sid}", json={"enabled": False})
    assert resp.status_code == 200
    resp = client.get("/api/sources")
    item = next(s for s in resp.get_json()["sources"] if s["id"] == sid)
    assert item["enabled"] is False


def test_delete_api_sources(client, seed_db):
    resp = client.post("/api/sources", json={"name": "Y", "url": "https://y/", "lang": "EN"})
    sid = resp.get_json()["source"]["id"]
    resp = client.delete(f"/api/sources/{sid}")
    assert resp.status_code == 204
    resp = client.get("/api/sources")
    assert not any(s["id"] == sid for s in resp.get_json()["sources"])
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_app.py -v -k api_sources`
Expected: FAIL.

- [ ] **Step 3: Add the endpoints**

In `its_briefing/app.py`, append to `create_app()`:

```python
    def _validate_source_payload(payload: dict, *, partial: bool = False) -> tuple[dict, list[str]]:
        from urllib.parse import urlparse
        errors: list[str] = []
        out: dict = {}
        if "name" in payload or not partial:
            name = (payload.get("name") or "").strip()
            if not name:
                errors.append("name is required")
            else:
                out["name"] = name
        if "url" in payload or not partial:
            url = (payload.get("url") or "").strip()
            parsed = urlparse(url)
            if not url or not parsed.scheme or not parsed.netloc:
                errors.append("url must be an absolute http(s) URL")
            else:
                out["url"] = url
        if "lang" in payload or not partial:
            lang = (payload.get("lang") or "").strip().upper()
            if lang not in ("EN", "DE"):
                errors.append("lang must be 'EN' or 'DE'")
            else:
                out["lang"] = lang
        if "enabled" in payload:
            out["enabled"] = bool(payload["enabled"])
        return out, errors

    @app.route("/api/sources", methods=["POST"])
    def api_sources_create():
        from its_briefing import db as _db
        payload = request.get_json(silent=True) or {}
        data, errors = _validate_source_payload(payload, partial=False)
        if errors:
            return jsonify({"errors": errors}), 400
        conn = _db.get_connection()
        try:
            _db.init_schema(conn)
            try:
                sid = _db.create_source(
                    conn,
                    name=data["name"],
                    url=data["url"],
                    lang=data["lang"],
                    enabled=data.get("enabled", True),
                )
            except sqlite3.IntegrityError:
                return jsonify({"errors": ["name must be unique"]}), 400
            row = _db.get_source(conn, sid)
        finally:
            conn.close()
        return jsonify({"source": dict(row) | {"id": sid}}), 201

    @app.route("/api/sources/<int:source_id>", methods=["PATCH"])
    def api_sources_update(source_id: int):
        from its_briefing import db as _db
        payload = request.get_json(silent=True) or {}
        data, errors = _validate_source_payload(payload, partial=True)
        if errors:
            return jsonify({"errors": errors}), 400
        conn = _db.get_connection()
        try:
            _db.init_schema(conn)
            if _db.get_source(conn, source_id) is None:
                return jsonify({"errors": ["not found"]}), 404
            try:
                _db.update_source(conn, source_id, data)
            except sqlite3.IntegrityError:
                return jsonify({"errors": ["name must be unique"]}), 400
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.route("/api/sources/<int:source_id>", methods=["DELETE"])
    def api_sources_delete(source_id: int):
        from its_briefing import db as _db
        conn = _db.get_connection()
        try:
            _db.init_schema(conn)
            _db.delete_source(conn, source_id)
        finally:
            conn.close()
        return ("", 204)
```

Also `import sqlite3` at the top of `app.py`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_app.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/app.py tests/test_app.py
git commit -m "feat(app): source CRUD JSON endpoints"
```

---

## Task 17: Check + check-all + check-status endpoints

**Files:**
- Modify: `its_briefing/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_app.py`:

```python
def test_post_check_returns_job_id(client, seed_db, monkeypatch):
    # seed one source
    resp = client.post("/api/sources", json={"name": "S", "url": "https://s/", "lang": "EN"})
    sid = resp.get_json()["source"]["id"]
    # avoid hitting the real network
    from its_briefing import sources as src_mod
    monkeypatch.setattr(src_mod, "start_health_check_job", lambda source_ids: "fake-job-id")

    resp = client.post(f"/api/sources/{sid}/check")
    assert resp.status_code == 202
    assert resp.get_json()["job_id"] == "fake-job-id"


def test_post_check_all_returns_job_id(client, seed_db, monkeypatch):
    from its_briefing import sources as src_mod
    monkeypatch.setattr(src_mod, "start_health_check_job", lambda source_ids: "fake-all")
    resp = client.post("/api/sources/check-all")
    assert resp.status_code == 202
    assert resp.get_json()["job_id"] == "fake-all"


def test_get_check_status(client, monkeypatch):
    from its_briefing import sources as src_mod
    monkeypatch.setattr(
        src_mod, "get_check_job",
        lambda jid: {"state": "done", "results": {1: {"status": "ok", "error": None}}} if jid == "j1" else None,
    )
    resp = client.get("/api/sources/check-status?job_id=j1")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["state"] == "done"


def test_get_check_status_unknown_job(client, monkeypatch):
    from its_briefing import sources as src_mod
    monkeypatch.setattr(src_mod, "get_check_job", lambda jid: None)
    resp = client.get("/api/sources/check-status?job_id=nope")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_app.py -v -k check`
Expected: FAIL.

- [ ] **Step 3: Add the endpoints**

In `its_briefing/app.py`, **import sources at the top** so monkey-patching works:

```python
from its_briefing import sources
```

Add inside `create_app()`:

```python
    @app.route("/api/sources/<int:source_id>/check", methods=["POST"])
    def api_sources_check(source_id: int):
        job_id = sources.start_health_check_job([source_id])
        return jsonify({"job_id": job_id}), 202

    @app.route("/api/sources/check-all", methods=["POST"])
    def api_sources_check_all():
        job_id = sources.start_health_check_job(None)
        return jsonify({"job_id": job_id}), 202

    @app.route("/api/sources/check-status", methods=["GET"])
    def api_sources_check_status():
        job_id = request.args.get("job_id", "")
        job = sources.get_check_job(job_id)
        if job is None:
            return jsonify({"error": "unknown job_id"}), 404
        return jsonify(job)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_app.py -v -k check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/app.py tests/test_app.py
git commit -m "feat(app): health-check job endpoints"
```

---

## Task 18: Diagnose endpoint

**Files:**
- Modify: `its_briefing/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_app.py`:

```python
def test_post_diagnose_persists_suggestion(client, seed_db, monkeypatch):
    resp = client.post("/api/sources", json={"name": "D", "url": "https://d/", "lang": "EN"})
    sid = resp.get_json()["source"]["id"]

    # set last_status=failed
    from its_briefing import db as _db
    conn = _db.get_connection()
    try:
        _db.record_source_check_result(conn, sid, status="failed", error="HTTP 503")
    finally:
        conn.close()

    from its_briefing import sources as src_mod
    monkeypatch.setattr(
        src_mod, "diagnose_failure",
        lambda **kw: ("Likely throttling — retry in 1h", None),
    )

    resp = client.post(f"/api/sources/{sid}/diagnose")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "throttling" in body["suggestion"]

    # Check it was persisted into last_diagnosis
    list_resp = client.get("/api/sources")
    item = next(s for s in list_resp.get_json()["sources"] if s["id"] == sid)
    assert "throttling" in (item["last_diagnosis"] or "")


def test_post_diagnose_handles_llm_failure(client, seed_db, monkeypatch):
    resp = client.post("/api/sources", json={"name": "E", "url": "https://e/", "lang": "EN"})
    sid = resp.get_json()["source"]["id"]
    from its_briefing import db as _db
    conn = _db.get_connection()
    try:
        _db.record_source_check_result(conn, sid, status="failed", error="HTTP 503")
    finally:
        conn.close()
    from its_briefing import sources as src_mod
    monkeypatch.setattr(src_mod, "diagnose_failure", lambda **kw: (None, "boom"))

    resp = client.post(f"/api/sources/{sid}/diagnose")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["suggestion"] is None
    assert body["error"] == "boom"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_app.py -v -k diagnose`
Expected: FAIL.

- [ ] **Step 3: Add the endpoint**

In `its_briefing/app.py`, inside `create_app()`:

```python
    @app.route("/api/sources/<int:source_id>/diagnose", methods=["POST"])
    def api_sources_diagnose(source_id: int):
        from its_briefing import db as _db
        conn = _db.get_connection()
        try:
            _db.init_schema(conn)
            row = _db.get_source(conn, source_id)
            if row is None:
                return jsonify({"error": "not found"}), 404
            settings = _db.get_settings(conn)
        finally:
            conn.close()

        if not row["last_error"]:
            return jsonify({"suggestion": None, "error": "no error to diagnose"}), 400

        suggestion, error = sources.diagnose_failure(
            source_name=row["name"],
            url=row["url"],
            last_error=row["last_error"],
            settings=settings,
        )

        # Persist suggestion (cleared automatically on next status flip).
        if suggestion is not None:
            conn = _db.get_connection()
            try:
                _db.update_source(conn, source_id, {"last_diagnosis": suggestion})
            finally:
                conn.close()

        return jsonify({"suggestion": suggestion, "error": error})
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_app.py -v -k diagnose`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/app.py tests/test_app.py
git commit -m "feat(app): /api/sources/<id>/diagnose endpoint"
```

---

## Task 19: `sources.html` template (table + modal + JS)

**Files:**
- Replace: `its_briefing/templates/sources.html`

This is a UI-heavy task with no unit tests of its own (route tests already cover the data). Manual verification via the dev server.

- [ ] **Step 1: Replace `its_briefing/templates/sources.html`**

Overwrite with:

```html
<!doctype html>
<html lang="en" class="h-full">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sources — ITS Briefing</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    html, body { font-family: 'Inter', system-ui, sans-serif; }
    .mono { font-family: 'JetBrains Mono', monospace; }
    .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .dot-ok       { background: #10b981; }
    .dot-failed   { background: #ef4444; }
    .dot-checking { background: #f59e0b; animation: pulse 1.2s infinite; }
    .dot-disabled { background: #475569; }
    .dot-unknown  { background: #cbd5e1; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
    .row-disabled { opacity: 0.55; }
  </style>
</head>
<body class="h-full bg-slate-900 text-slate-100">
  <div class="max-w-6xl mx-auto px-6 py-10">
    <header class="mb-8 border-b border-slate-800 pb-4 flex items-center justify-between">
      <h1 class="text-2xl font-bold text-cyan-400">Sources</h1>
      <a href="/" class="text-sm text-slate-400 hover:text-cyan-300">← Back to briefing</a>
    </header>

    <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
      <div id="counts" class="flex gap-2 text-xs">
        <button data-filter="ok"       class="filter-btn px-2 py-1 rounded bg-slate-800 text-emerald-300">● <span id="count-ok">0</span> ok</button>
        <button data-filter="failed"   class="filter-btn px-2 py-1 rounded bg-slate-800 text-red-300">● <span id="count-failed">0</span> failed</button>
        <button data-filter="checking" class="filter-btn px-2 py-1 rounded bg-slate-800 text-amber-300">○ <span id="count-checking">0</span> checking</button>
        <button data-filter="all"      class="filter-btn px-2 py-1 rounded bg-slate-800 text-slate-300">all</button>
      </div>
      <div class="flex gap-2">
        <button id="add-btn"       class="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm">+ Add</button>
        <button id="check-all-btn" class="px-3 py-1.5 rounded bg-cyan-500 text-slate-900 font-medium hover:bg-cyan-400 text-sm">Check all</button>
      </div>
    </div>

    <div id="empty-banner" class="hidden mb-4 p-3 rounded bg-slate-800/60 border border-slate-700 text-slate-300 text-sm">
      Sources have not been checked yet — click <strong>Check all</strong> to verify.
    </div>

    <div class="rounded-lg border border-slate-800 overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-slate-800/60 text-slate-400 uppercase text-[10px] tracking-wider">
          <tr>
            <th class="px-3 py-2 w-8"></th>
            <th class="px-3 py-2 text-left">Name</th>
            <th class="px-3 py-2 text-left">URL</th>
            <th class="px-3 py-2 text-left">Lang</th>
            <th class="px-3 py-2 text-left">Last checked</th>
            <th class="px-3 py-2 text-left">Last error</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
  </div>

  <!-- Add/Edit modal -->
  <div id="modal" class="hidden fixed inset-0 bg-black/60 flex items-center justify-center z-50">
    <div class="bg-slate-800 border border-slate-700 rounded-lg p-6 w-full max-w-md">
      <h2 id="modal-title" class="text-lg font-semibold mb-4 text-cyan-300">Add source</h2>
      <form id="modal-form" class="space-y-3">
        <input type="hidden" id="m-id">
        <label class="block">
          <span class="text-xs text-slate-400">Name</span>
          <input id="m-name" type="text" class="mt-1 w-full rounded bg-slate-900 border border-slate-700 p-2 text-sm">
        </label>
        <label class="block">
          <span class="text-xs text-slate-400">URL</span>
          <input id="m-url" type="text" class="mt-1 w-full rounded bg-slate-900 border border-slate-700 p-2 text-sm mono">
        </label>
        <div class="flex gap-4 items-center">
          <label class="text-xs text-slate-400">Lang:</label>
          <label class="flex items-center gap-1"><input type="radio" name="m-lang" value="EN" checked> EN</label>
          <label class="flex items-center gap-1"><input type="radio" name="m-lang" value="DE"> DE</label>
          <label class="flex items-center gap-2 ml-auto"><input id="m-enabled" type="checkbox" checked> Enabled</label>
        </div>
        <div id="modal-errors" class="text-xs text-red-300"></div>
        <div class="flex justify-end gap-2 pt-2">
          <button type="button" id="modal-cancel" class="px-3 py-1.5 rounded bg-slate-700 hover:bg-slate-600 text-sm">Cancel</button>
          <button type="submit"                   class="px-3 py-1.5 rounded bg-cyan-500 text-slate-900 hover:bg-cyan-400 text-sm font-medium">Save</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    const $ = (s) => document.querySelector(s);
    let SOURCES = [];
    let activeFilter = "all";
    let pollTimer = null;

    function fmtRelative(iso) {
      if (!iso) return "never";
      const d = new Date(iso);
      const sec = Math.floor((Date.now() - d.getTime()) / 1000);
      if (sec < 60) return `${sec}s ago`;
      if (sec < 3600) return `${Math.floor(sec/60)}m ago`;
      if (sec < 86400) return `${Math.floor(sec/3600)}h ago`;
      return `${Math.floor(sec/86400)}d ago`;
    }

    function dotClass(s) {
      if (!s.enabled) return "dot-disabled";
      if (s.last_status === "ok") return "dot-ok";
      if (s.last_status === "failed") return "dot-failed";
      if (s.last_status === "checking") return "dot-checking";
      return "dot-unknown";
    }

    function render() {
      const rows = $("#rows");
      rows.innerHTML = "";
      let okN = 0, failN = 0, checkN = 0;
      for (const s of SOURCES) {
        if (s.enabled && s.last_status === "ok") okN++;
        if (s.enabled && s.last_status === "failed") failN++;
        if (s.enabled && s.last_status === "checking") checkN++;
        if (activeFilter !== "all") {
          if (activeFilter === "checking" && s.last_status !== "checking") continue;
          if (activeFilter === "ok"       && (!s.enabled || s.last_status !== "ok")) continue;
          if (activeFilter === "failed"   && (!s.enabled || s.last_status !== "failed")) continue;
        }
        const tr = document.createElement("tr");
        tr.className = "border-t border-slate-800 hover:bg-slate-800/40 " + (s.enabled ? "" : "row-disabled");
        tr.innerHTML = `
          <td class="px-3 py-2"><span class="dot ${dotClass(s)}"></span></td>
          <td class="px-3 py-2">${escapeHtml(s.name)}${s.enabled ? "" : ' <span class="text-xs text-slate-500">(disabled)</span>'}</td>
          <td class="px-3 py-2 mono text-xs text-slate-400 truncate max-w-xs" title="${escapeHtml(s.url)}">${escapeHtml(s.url)}</td>
          <td class="px-3 py-2"><span class="px-1.5 py-0.5 rounded bg-slate-700 text-slate-300 mono text-[10px]">${s.lang}</span></td>
          <td class="px-3 py-2 text-xs text-slate-400" title="${s.last_checked_at || ''}">${fmtRelative(s.last_checked_at)}</td>
          <td class="px-3 py-2 text-xs text-red-300 truncate max-w-xs" title="${escapeHtml(s.last_error||'')}">${escapeHtml(s.last_error || '')}</td>
          <td class="px-3 py-2 text-right whitespace-nowrap">
            <button class="check-one px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-xs" data-id="${s.id}">Check</button>
            <button class="edit-one  px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-xs" data-id="${s.id}">Edit</button>
            ${s.last_status === "failed" ? `<button class="diag-one px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-xs text-amber-300" data-id="${s.id}">Analyze ▾</button>` : ''}
            <button class="del-one   px-2 py-1 rounded bg-slate-800 hover:bg-red-900/50 text-xs text-red-300" data-id="${s.id}">×</button>
          </td>`;
        rows.appendChild(tr);

        if (s.last_diagnosis) {
          const dr = document.createElement("tr");
          dr.className = "bg-slate-900/60 border-t border-slate-800";
          dr.innerHTML = `<td colspan="7" class="px-6 py-3 text-xs text-slate-300">
            <div class="font-semibold text-slate-200 mb-1">${escapeHtml(s.name)} — Diagnosis</div>
            <div class="text-slate-400 mb-1">Error: <span class="mono">${escapeHtml(s.last_error||'')}</span></div>
            <div>🤖 ${escapeHtml(s.last_diagnosis)}</div>
          </td>`;
          rows.appendChild(dr);
        }
      }
      $("#count-ok").textContent = okN;
      $("#count-failed").textContent = failN;
      $("#count-checking").textContent = checkN;
      $("#empty-banner").classList.toggle("hidden", SOURCES.some(s => s.last_checked_at));
    }

    function escapeHtml(s) {
      return (s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    }

    async function loadSources() {
      const r = await fetch("/api/sources");
      const j = await r.json();
      SOURCES = j.sources;
      render();
      const anyChecking = SOURCES.some(s => s.last_status === "checking");
      if (anyChecking && !pollTimer) startPolling();
      if (!anyChecking && pollTimer) stopPolling();
    }

    function startPolling() {
      if (pollTimer) return;
      pollTimer = setInterval(loadSources, 1000);
    }
    function stopPolling() {
      clearInterval(pollTimer); pollTimer = null;
    }

    document.querySelectorAll(".filter-btn").forEach(b =>
      b.addEventListener("click", () => { activeFilter = b.dataset.filter; render(); }));

    $("#check-all-btn").addEventListener("click", async () => {
      await fetch("/api/sources/check-all", { method: "POST" });
      startPolling();
    });

    $("#rows").addEventListener("click", async (e) => {
      const t = e.target;
      const id = t.dataset.id;
      if (!id) return;
      if (t.classList.contains("check-one")) {
        await fetch(`/api/sources/${id}/check`, { method: "POST" });
        startPolling();
      } else if (t.classList.contains("edit-one")) {
        const s = SOURCES.find(x => String(x.id) === id);
        openModal(s);
      } else if (t.classList.contains("del-one")) {
        if (!confirm("Delete this source?")) return;
        await fetch(`/api/sources/${id}`, { method: "DELETE" });
        loadSources();
      } else if (t.classList.contains("diag-one")) {
        t.disabled = true; t.textContent = "Analyzing…";
        const r = await fetch(`/api/sources/${id}/diagnose`, { method: "POST" });
        const j = await r.json();
        if (j.suggestion) loadSources();
        else alert("Couldn't analyze: " + (j.error || "unknown"));
      }
    });

    function openModal(src) {
      $("#modal-title").textContent = src ? "Edit source" : "Add source";
      $("#m-id").value = src ? src.id : "";
      $("#m-name").value = src ? src.name : "";
      $("#m-url").value = src ? src.url : "";
      document.querySelector(`input[name="m-lang"][value="${src ? src.lang : 'EN'}"]`).checked = true;
      $("#m-enabled").checked = src ? src.enabled : true;
      $("#modal-errors").textContent = "";
      $("#modal").classList.remove("hidden");
    }

    $("#add-btn").addEventListener("click", () => openModal(null));
    $("#modal-cancel").addEventListener("click", () => $("#modal").classList.add("hidden"));

    $("#modal-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const id = $("#m-id").value;
      const payload = {
        name: $("#m-name").value.trim(),
        url: $("#m-url").value.trim(),
        lang: document.querySelector('input[name="m-lang"]:checked').value,
        enabled: $("#m-enabled").checked,
      };
      const url = id ? `/api/sources/${id}` : "/api/sources";
      const method = id ? "PATCH" : "POST";
      const r = await fetch(url, { method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload) });
      if (!r.ok) {
        const j = await r.json();
        $("#modal-errors").textContent = (j.errors || ["error"]).join("; ");
        return;
      }
      $("#modal").classList.add("hidden");
      loadSources();
    });

    loadSources();
  </script>
</body>
</html>
```

- [ ] **Step 2: Manual verification**

Activate the venv: `source .venv/Scripts/activate`
Run: `python -m its_briefing` (in another terminal: `curl -X POST http://127.0.0.1:8089/generate` to make sure pipeline still works).
Visit `http://127.0.0.1:8089/sources` in a browser.

Verify:
- All sources render in the table.
- "Check all" turns dots orange briefly, then green/red.
- Clicking "Edit" populates the modal; Save persists.
- Clicking "+ Add" opens the modal with empty fields.
- A red row shows "Analyze ▾"; clicking it eventually shows a diagnosis row beneath.
- The filter chips at the top filter rows.

- [ ] **Step 3: Commit**

```bash
git add its_briefing/templates/sources.html
git commit -m "feat(ui): sources page with table, modal, polling, analyze expander"
```

---

## Task 20: Briefing-page nav dropdown + last_error strip

**Files:**
- Modify: `its_briefing/templates/briefing.html`

- [ ] **Step 1: Replace the gear-icon header block**

In `its_briefing/templates/briefing.html`, replace the existing `<a href="/settings" ...>⚙</a>` block (around line 26) with a small dropdown:

```html
        <div class="relative" id="nav-menu">
          <button id="nav-trigger" class="text-slate-400 hover:text-cyan-300" aria-label="Menu">⚙</button>
          <div id="nav-dropdown" class="hidden absolute right-0 mt-1 w-40 rounded bg-slate-800 border border-slate-700 shadow-lg z-10">
            <a href="/sources"  class="block px-3 py-2 text-sm hover:bg-slate-700">Sources</a>
            <a href="/settings" class="block px-3 py-2 text-sm hover:bg-slate-700">Settings</a>
            <a href="/health"   class="block px-3 py-2 text-sm hover:bg-slate-700 mono text-slate-400">/health</a>
          </div>
        </div>
```

- [ ] **Step 2: Add the last_error strip**

In `its_briefing/templates/briefing.html`, just inside the `<section class="mb-12 ...">` for the Executive Summary (before the `<h2>` line), add:

```html
        {% if briefing.last_error %}
          <div class="mb-4 p-3 rounded bg-amber-900/30 border border-amber-800 text-amber-200 text-xs">
            ⚠ Last summary attempt failed: <code class="mono">{{ briefing.last_error }}</code> — section may be incomplete.
          </div>
        {% endif %}
```

- [ ] **Step 3: Add the dropdown JS**

Append to the existing `<script>` block in `briefing.html`:

```javascript
    const navTrigger = document.getElementById('nav-trigger');
    const navDropdown = document.getElementById('nav-dropdown');
    if (navTrigger && navDropdown) {
      navTrigger.addEventListener('click', (e) => {
        e.stopPropagation();
        navDropdown.classList.toggle('hidden');
      });
      document.addEventListener('click', () => navDropdown.classList.add('hidden'));
    }
```

- [ ] **Step 4: Manual verification**

Run `python -m its_briefing`, open `http://127.0.0.1:8089/`. Click the gear icon — dropdown opens with three links. If the latest briefing has `last_error`, the amber strip is visible.

- [ ] **Step 5: Run all tests**

Run: `pytest -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add its_briefing/templates/briefing.html
git commit -m "feat(ui): nav dropdown on briefing page; render last_error strip"
```

---

## Task 21: Update CLAUDE.md & docs

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update relevant sections**

In `CLAUDE.md`, update these places:

1. **"Configuration is the seam"** section: note that sources now live in the DB, YAML is one-time seed only.

```markdown
The RSS source list lives in the SQLite `sources` table — `config/sources.yaml` is read **once** at first DB init via `db.seed_sources_from_yaml()` and ignored afterward. Add/remove/edit feeds via `/sources` in the UI (or `db.create_source` / `db.update_source` in code). The topic categories still live in `config/categories.yaml` and are reloaded on every run; adding a category is a YAML edit.
```

2. **"Settings live in SQLite"** section: append a paragraph about sources.

```markdown
Sources live in the same DB (table `sources`) with health columns (`last_status`, `last_checked_at`, `last_error`, `last_diagnosis`) updated by manual checks (via `its_briefing.sources.start_health_check_job`) and by the daily fetch back-feed inside `generate.run()`. The `/sources` page renders this table and offers on-demand LLM diagnosis via `its_briefing.sources.diagnose_failure()`.
```

3. **"LM Studio context-length gotcha"** section: replace the warning with the new behavior.

```markdown
**Per-section summarization.** `llm.build_summary()` produces the four executive-summary sections via four separate LLM calls, each fed only articles classified into that section's mapped categories (see `SECTION_CATEGORIES` in `llm.py`). This keeps each prompt comfortably under any reasonable context window. If a section call fails after one retry, that section becomes empty and the most recent error is captured into `briefings.last_error`, which the briefing page renders as an amber strip under the Executive Summary header. Total summary failure (every populated section failed) still falls back to `ExecutiveSummary.placeholder`.
```

- [ ] **Step 2: Run all tests one more time**

Run: `pytest -v`
Expected: ALL PASS.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): describe DB-backed sources + per-section summarization"
```

---

## Self-review

- **Spec coverage:** sources DB (Task 2-4), Source model (Task 5), DB-backed `load_sources` (Task 6), pipeline integration (Task 7), `briefings.last_error` (Tasks 8-9), per-section build_summary (Task 10) and its plumbing into the pipeline (Task 11), health-check module (Tasks 12-14), all routes (Tasks 15-18), UI (Tasks 19-20), categories update (Task 1), docs (Task 21). All sections of the spec are covered.
- **Placeholder scan:** none. All steps include exact code, file paths, and commands.
- **Type consistency:** `build_summary` returns `tuple[ExecutiveSummary, Optional[str]]` consistently from Task 10 onward; all test mocks are updated in the same task. `Briefing.last_error: Optional[str]` is the same name across model, db, generate, and template. `start_health_check_job` / `get_check_job` / `health_check_one` / `diagnose_failure` keep their signatures across sources.py, app.py, and tests. `record_source_check_result` keyword args (`status`, `error`) match between db.py and sources.py.

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-29-sources-management.md`. Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
