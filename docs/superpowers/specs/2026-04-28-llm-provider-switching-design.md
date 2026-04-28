# LLM Provider Switching + SQLite Persistence — Design

**Date:** 2026-04-28
**Status:** Approved (awaiting plan)

## Problem

Two related issues:

1. **Bug.** The current `llm.py` is hardcoded to Ollama's API shape (`POST /api/chat`, response at `data["message"]["content"]`). The user has reconfigured `.env` to point at an LM Studio instance, which speaks an OpenAI-compatible API (`POST /v1/chat/completions`, response at `data["choices"][0]["message"]["content"]`). Result: every classify call raises `KeyError: 'message'` and falls back to `Uncategorized`.

2. **Feature.** The user wants to switch between Ollama and LM Studio at runtime via a settings page in the UI, without editing files or restarting the process.

In addition, the user has asked to introduce a flat-file SQLite database to hold settings, articles, briefings, and a generation-runs log. Existing per-day JSON files in `cache/` are abandoned (start-fresh migration).

## Goals

- Replace the Ollama-specific HTTP code with a small client abstraction that supports both Ollama and LM Studio.
- Make the active provider, base URL, and model name editable from a `/settings` page in the UI.
- Add a "Test connection" affordance that calls the provider's model-list endpoint and reports back without saving.
- Move runtime state (settings + articles + briefings + generation-runs log) into a SQLite database at `cache/its_briefing.db`. `.env` becomes a one-shot seed; the DB is the source of truth thereafter.
- Preserve the existing three-layer error handling discipline (per-feed, per-LLM-call, per-pipeline).
- Keep the public surface of `storage.py` stable so `app.py` and `generate.py` change minimally.

## Non-goals

- Migrating existing `cache/briefing-*.json` files into the DB. Per user decision: start fresh.
- Authentication or authorization on `/settings`. Same trust model as the existing `POST /generate` (LAN-only, behind Nginx).
- Editing `config/sources.yaml` or `config/categories.yaml` from the UI. Those remain repo-managed.
- A history page or runs-log viewer. The data is captured; rendering UI is a future story.
- Supporting additional LLM providers (Claude API, OpenAI, etc.). The abstraction allows it later but only Ollama and LM Studio are in scope now.

## Architecture

### Module layout (changes only)

```
its_briefing/
├── db.py            ← NEW
├── llm.py           ← REFACTOR: provider-agnostic client classes
├── storage.py       ← REWRITE: DB-backed; same public API
├── config.py        ← EXTEND: new Settings fields, DB-as-truth semantics
├── scheduler.py     ← EXTEND: add reschedule(hour, minute, tz)
├── app.py           ← ADD: /settings GET+POST, /api/test-connection POST
├── generate.py      ← MINOR: read settings from DB, write run rows
└── templates/
    ├── briefing.html  ← gear icon linking to /settings
    └── settings.html  ← NEW
```

### LLM client abstraction (`llm.py`)

Two simple classes implementing a `chat(prompt) -> str` method, plus a model-listing method for the test-connection endpoint:

```python
class OllamaClient:
    def chat(self, prompt: str) -> str:
        # POST {base_url}/api/chat with {"model": ..., "format": "json", "stream": False,
        #                                 "messages": [{"role": "user", "content": prompt}]}
        # returns data["message"]["content"]

    def list_models(self) -> list[str]:
        # GET {base_url}/api/tags  →  [m["name"] for m in data["models"]]


class LMStudioClient:
    def chat(self, prompt: str) -> str:
        # POST {base_url}/v1/chat/completions with {"model": ..., "response_format": {"type":"json_object"},
        #                                            "messages": [{"role": "user", "content": prompt}]}
        # returns data["choices"][0]["message"]["content"]

    def list_models(self) -> list[str]:
        # GET {base_url}/v1/models  →  [m["id"] for m in data["data"]]


def make_client(settings: Settings) -> OllamaClient | LMStudioClient:
    if settings.llm_provider == "ollama":
        return OllamaClient(settings.llm_base_url, settings.llm_model)
    return LMStudioClient(settings.llm_base_url, settings.llm_model)
```

`classify_article(article, categories, settings)` and `build_summary(articles, settings, target_date)` keep their signatures. Internally they call `make_client(settings).chat(prompt)`. The four existing fallback paths (HTTP error, invalid JSON, unknown category, summary retry) remain — both clients raise a single `LLMClientError` subclass on any failure, which the higher-level functions catch alongside the existing `httpx.HTTPError | json.JSONDecodeError | KeyError | TypeError` set.

The bug is fixed by routing LM-Studio-shaped responses through `LMStudioClient.chat` which reads `data["choices"][0]["message"]["content"]`.

### Database (`db.py` + `cache/its_briefing.db`)

SQLite via Python's stdlib `sqlite3`. Single file, WAL journal mode, foreign keys on. Connection helper returns rows with `sqlite3.Row` factory.

#### Schema

```sql
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL          -- JSON-encoded value
);

CREATE TABLE articles (
    id          TEXT PRIMARY KEY,    -- sha256(link)[:16]
    source      TEXT NOT NULL,
    source_lang TEXT NOT NULL,       -- 'EN' | 'DE'
    title       TEXT NOT NULL,
    link        TEXT NOT NULL,
    published   TEXT NOT NULL,       -- ISO-8601 UTC
    summary     TEXT NOT NULL,
    category    TEXT,
    first_seen  TEXT NOT NULL        -- ISO-8601 UTC; first generation that saw this article
);
CREATE INDEX idx_articles_published ON articles(published);

CREATE TABLE briefings (
    date           TEXT PRIMARY KEY,    -- 'YYYY-MM-DD'
    generated_at   TEXT NOT NULL,
    summary_json   TEXT NOT NULL,       -- JSON of ExecutiveSummary
    failed_sources TEXT NOT NULL        -- JSON array of strings
);

CREATE TABLE briefing_articles (
    briefing_date TEXT NOT NULL,
    article_id    TEXT NOT NULL,
    PRIMARY KEY (briefing_date, article_id),
    FOREIGN KEY (briefing_date) REFERENCES briefings(date) ON DELETE CASCADE,
    FOREIGN KEY (article_id)    REFERENCES articles(id)
);

CREATE TABLE generation_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    succeeded     INTEGER,                -- 0 / 1, nullable until finished
    article_count INTEGER,
    error         TEXT
);

CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
```

#### Public API of `db.py`

- `get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection`
- `init_schema(conn)` — idempotent `CREATE TABLE IF NOT EXISTS` for all tables, sets `schema_version = 1` on first init.
- `seed_settings_from_env(conn, env_settings: Settings)` — only writes if `settings` table is empty.
- `get_settings(conn) -> Settings`
- `update_settings(conn, partial: dict)` — upsert per key.
- `upsert_article(conn, article: Article)` — inserts on first sight, updates `category` and `summary` on subsequent runs (the article's `id` is stable from `sha256(link)[:16]`).
- `save_briefing(conn, briefing: Briefing)` — single transaction: upsert all articles, upsert briefing row, replace join rows for that date.
- `latest_briefing(conn) -> Briefing | None`
- `record_run_start(conn) -> int` — returns the new run id.
- `record_run_finish(conn, run_id, succeeded, article_count, error)`.

### Settings precedence

1. **First boot** (settings table empty): `Settings.from_env()` reads `.env` defaults, then `db.seed_settings_from_env()` writes every value into the DB.
2. **Subsequent boots**: `db.get_settings()` reads the DB; `.env` is ignored for the keys it covers.
3. **Process-bound exceptions**: `FLASK_HOST`, `FLASK_PORT`, `LOG_LEVEL` are read from env on every boot (they bind to the process at startup, not editable in the UI).
4. **Runtime updates** (UI): `POST /settings` calls `db.update_settings()`. The next pipeline run reads the new values. If schedule fields changed, `app.py` calls `scheduler.reschedule()` so the cron trigger updates without restart.

### Settings model fields

`Settings` (in `config.py`) gains:

| Field | Type | Default (env var) | UI-editable |
|---|---|---|---|
| `llm_provider` | `Literal["ollama","lmstudio"]` | `LLM_PROVIDER` (default `"ollama"`) | yes |
| `llm_base_url` | `str` | `LLM_BASE_URL` (default `http://localhost:11434`) | yes |
| `llm_model` | `str` | `LLM_MODEL` (default `llama3.1:8b`) | yes |
| `timezone` | `str` | `TIMEZONE` | yes |
| `schedule_hour` | `int` | `SCHEDULE_HOUR` | yes |
| `schedule_minute` | `int` | `SCHEDULE_MINUTE` | yes |
| `flask_host` | `str` | `FLASK_HOST` | no (process-bound) |
| `flask_port` | `int` | `FLASK_PORT` | no (process-bound) |
| `log_level` | `str` | `LOG_LEVEL` | no (process-bound) |

Legacy aliases: `OLLAMA_BASE_URL` and `OLLAMA_MODEL` are accepted as fallbacks for `LLM_BASE_URL` and `LLM_MODEL` so existing `.env` files still work on first boot. After seeding, the DB is the truth.

### Routes

```
GET  /settings                  → renders settings.html populated from db.get_settings()
POST /settings                  → validates form, writes via db.update_settings(), reschedules
                                  if schedule fields changed, redirects to /settings with
                                  a success flash; on validation error, re-renders form
                                  with field-level error messages.
POST /api/test-connection       → JSON in/out (no DB write)
       request:  {"provider": "ollama"|"lmstudio", "base_url": str, "model": str}
       response: {"ok": bool, "models": [str], "error": str|null, "latency_ms": int}
```

### Settings page UI (`settings.html`)

Dark-mode form matching the existing briefing.html palette. Sections:

- **LLM Provider** — radio (Ollama / LM Studio).
- **Base URL** — text input; placeholder shows the default for the selected provider.
- **Model** — text input by default; a successful Test connection populates it as a `<select>` with discovered models. Manual typing reverts it to free-text.
- **Test connection** — button hits `POST /api/test-connection` with the current form values. Inline status: ✓ "ok (12 models, 230 ms)" or ✗ "<error>". No save side effect.
- **Schedule** — hour (0–23) + minute (0–59) + IANA timezone string.
- **Save** + **Cancel** buttons.

Validation is server-side. The briefing page header gets a small gear icon linking to `/settings`.

### Scheduler

`scheduler.py` exposes:

```python
def reschedule(hour: int, minute: int, timezone: str) -> None:
    # remove the existing job, re-add with new CronTrigger; raises ValueError on bad timezone
```

Called from `POST /settings` after a successful DB write when any of those three values changed. Bad timezone strings surface as a form-level error and abort the save.

## Data flow

### Pipeline run (no behavior change beyond persistence)

```
generate.run()
  ├─ db.get_settings()                     ← was: Settings.from_env()
  ├─ db.record_run_start()  → run_id
  ├─ fetch.fetch_all(sources)              (unchanged)
  ├─ for article: classify_article(article, categories, settings)
  │     └─ make_client(settings).chat(...)
  ├─ build_summary(articles, settings, target_date)
  │     └─ make_client(settings).chat(...) [+ retry]
  ├─ storage.save_briefing(briefing)       ← now writes to DB
  └─ db.record_run_finish(run_id, succeeded=True, ...)
```

On exception, the top-level `try/except` calls `record_run_finish(run_id, succeeded=False, error=str(exc))` and returns `None`, exactly as today.

### Settings update

```
User clicks Save  →  POST /settings
                     ├─ validate form
                     ├─ db.update_settings(...)
                     ├─ if schedule changed: scheduler.reschedule(...)
                     └─ redirect to /settings?saved=1
```

### Test connection

```
User clicks Test connection  →  POST /api/test-connection (form values, not DB)
                                ├─ build a transient client with provider/base_url/model
                                ├─ client.list_models()    [5s timeout]
                                └─ JSON response with ok/models/error/latency_ms
```

## Error handling

| Boundary | Failure | Behavior |
|---|---|---|
| `db.get_connection()` | DB file unwritable / corrupt | Raises at startup. Hard config problem; failing fast is correct. |
| `db.seed_settings_from_env` | Settings already populated | No-op (idempotent). |
| `OllamaClient.chat` / `LMStudioClient.chat` | HTTP error, malformed JSON, missing keys | Raises `LLMClientError`. Caught by `classify_article` (→ `Uncategorized`) or `build_summary` (→ retry once, then placeholder). |
| `*.list_models()` (test connection) | Same | Returned to client as `{ok: false, error: "..."}`; never raises to HTTP layer. |
| `POST /settings` | Invalid form values (bad provider, malformed URL, out-of-range hour/minute, bad timezone) | Re-renders form with field-level errors. Nothing written. |
| `scheduler.reschedule` | Bad timezone string | Surfaces as form error in `POST /settings`. Save is aborted. |
| `generate.run()` | Crashes mid-pipeline | Existing top-level `try/except` returns `None`. Adds: writes a `generation_runs` row with `succeeded=0` and the error summary. |

The three architectural failure layers from CLAUDE.md (per-feed, per-LLM-call, per-pipeline) are preserved. The DB adds an audit trail; it does not move the failure boundaries.

## Testing

| File | Coverage |
|---|---|
| `tests/test_db.py` (NEW) | Schema init idempotent · seed-from-env populates only when empty · settings round-trip · article upsert idempotent on `id` · briefing save creates join rows · `latest_briefing()` returns newest by date · runs table records start/finish. Each test gets an isolated DB via `tmp_path`. |
| `tests/test_llm.py` (UPDATE) | Existing 6 tests parameterized across `OllamaClient` and `LMStudioClient`. Each provider gets the four fallback paths (HTTP error, invalid JSON, unknown category, retry). Adds dedicated success and "missing 'choices' falls back" tests for `LMStudioClient` to lock the bug fix. |
| `tests/test_storage.py` (UPDATE) | Public API unchanged; assertions move from "JSON file exists" to "row exists in DB". Uses `tmp_path` for the DB file. |
| `tests/test_generate.py` (UPDATE) | Patches stay at `its_briefing.generate.<module>.<name>`. Adds: a `generation_runs` row records `succeeded=1` on success, `succeeded=0` with error text on a forced exception. |
| `tests/test_app.py` (NEW) | `GET /settings` renders 200 with current values · `POST /settings` writes to DB and redirects · `POST /settings` with bad timezone re-renders form with error · `POST /api/test-connection` with both providers (mocked) returns the expected `{ok, models}` shape. |
| `tests/test_config.py` (UPDATE) | `Settings.from_env()` still works. Adds: when DB empty, env values are seeded; when DB populated, env values are ignored on next boot. |

### Manual verification (per project rule for UI changes)

1. Run `python -m its_briefing`.
2. Open `http://127.0.0.1:8089/`, click the gear icon → `/settings` renders with seeded values.
3. With provider = LM Studio and base URL = `http://192.168.32.231:1234`, click **Test connection** → expect ✓ and a list of models.
4. Save settings; click **Rebuild now** on the briefing page → pipeline completes without the `'message'` `KeyError`; the briefing renders with classified articles.
5. Switch back to Ollama (assuming the user has a local Ollama with `llama3.1:8b` pulled) and verify the same flow works.

## Risks and trade-offs

- **Adding SQLite is an architectural shift.** The original CLAUDE.md leans on "no DB". We accept the trade-off because mutable runtime settings need durable storage, and once the DB is in, articles + briefings + runs are cheap incremental wins. Schema migrations become a thing (we add a `schema_version` table for the future).
- **`.env` is now a one-shot seed.** This is surprising if a user expects `.env` edits to take effect. We document this clearly in the README/CLAUDE.md and surface the same fields in the UI.
- **Inspectability changes.** `cat cache/briefing-*.json` is no longer how you inspect briefings. We accept this; the briefings render in the UI, and `sqlite3 cache/its_briefing.db` works for ad-hoc inspection.
- **No auth on `/settings`.** Inherits the existing trust model (LAN-only, Nginx in front). Documented in CLAUDE.md.

## Open questions

None at this point. The design covers the feature, the bug fix, and the data-store migration. Implementation can proceed.
