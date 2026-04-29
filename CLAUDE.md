# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

ITS-Briefing is a slim, standalone Python web app that aggregates 19 curated cybersecurity RSS feeds, classifies each article via a local LLM (Ollama or LM Studio), generates a structured AI executive summary once per day, and serves the result on a dark-mode web page. One process, no auth, a single SQLite file (`cache/its_briefing.db`) for settings + articles + briefings.

## Common commands

```bash
# Activate the venv (Windows Git Bash)
source .venv/Scripts/activate

# Install (editable + dev deps)
pip install -e ".[dev]"

# Run web app + scheduler (Flask on 127.0.0.1:8089, cron at 06:00 Europe/Berlin)
python -m its_briefing

# Force a full briefing build right now (bypasses the schedule)
python -m its_briefing.generate

# Tests
pytest                                            # all 64 tests
pytest tests/test_fetch.py -v                     # one file
pytest tests/test_llm.py::test_classify_article_returns_chosen_category -v  # one test
```

The app needs a reachable LLM (Ollama or LM Studio). For Ollama, pull a model once: `ollama pull llama3.1:8b`. For first-boot configuration set `LLM_PROVIDER`, `LLM_BASE_URL`, and `LLM_MODEL` in `.env` (these seed the DB once); after that, change provider/model via the `/settings` page in the UI. The legacy `OLLAMA_BASE_URL` / `OLLAMA_MODEL` env vars are still accepted as fallbacks during initial seeding.

## Architecture — the parts that matter

The whole app is **one Python process** that runs both Flask and APScheduler in-process. There is no message queue and no external cache layer. State lives in a single SQLite file at `cache/its_briefing.db` (settings, articles, briefings, run history) — see "Settings live in SQLite" below for the schema rationale.

### Module boundaries (deliberate)

```
its_briefing/
├── __main__.py    # Entry point: load .env → init DB → start scheduler → run Flask
├── app.py         # Flask factory + routes (/, /health, /settings, /api/test-connection, /generate)
├── scheduler.py   # APScheduler BackgroundScheduler wrapper (module-global _scheduler) + reschedule()
├── generate.py    # Pipeline orchestrator: fetch → classify → summarize → save → record run
├── fetch.py       # Concurrent RSS fetcher + 24h time-window filter
├── llm.py         # OllamaClient + LMStudioClient (make_client dispatches per settings.llm_provider)
├── storage.py     # Thin wrappers over db.py for save_briefing / load_briefing / latest_briefing
├── db.py          # SQLite connection + schema + CRUD (settings, articles, briefings, runs)
├── config.py      # Pydantic Settings + load_sources()/load_categories() from YAML
├── models.py      # Pydantic data classes: Article, Bullet, ExecutiveSummary, Briefing
└── templates/
    ├── briefing.html  # Daily briefing page (Tailwind via CDN)
    └── settings.html  # Settings form (provider/url/model/schedule + Test connection)
```

**Critical boundary rule:** `fetch`, `llm`, `storage`, and `db` are pure I/O modules with no Flask or scheduler dependency. They are trivially unit-testable. `generate.run()` is the **only** place that knows the order of pipeline operations. `app.py` only talks to `storage.load_briefing()`, `db.get_settings()` / `db.update_settings()`, and `generate.run()` — it never imports `fetch` or `llm` directly.

### Two pipeline entry points, one implementation

Both `python -m its_briefing.generate` (CLI) and the APScheduler cron job (06:00 Europe/Berlin daily) call the same `generate.run()` function. The Flask `POST /generate` route also calls it synchronously (which can take 1-2 minutes — Ollama latency × ~50 articles). The pipeline is wrapped in a top-level `try/except` so a crash returns `None` and never propagates to the scheduler.

`generate.run()` calls `load_dotenv()` itself so the CLI path also picks up `.env` (the web entry point also loads it; calling `load_dotenv` twice is idempotent).

### Error handling layers

Three failure modes, each handled at its own boundary so a failure in one place never breaks the rest:

1. **Single RSS feed unreachable / malformed** → caught in `fetch._fetch_one()`, source name appended to `failed_sources`, pipeline continues with the rest. Never raises.
2. **Ollama unreachable / returns garbage** → `llm.classify_article()` returns `"Uncategorized"`; `llm.build_summary()` summarizes section-by-section and falls back to an empty list per failed section, capturing the most recent error to `briefings.last_error`. Only when **every** populated section fails is `ExecutiveSummary.placeholder(target_date)` returned. Never raises.
3. **Pipeline crashes mid-run** → `generate.run()`'s top-level `try/except` logs the traceback and returns `None`. The scheduler keeps running. The previous day's briefing remains visible.

The frontend always renders the **most recent successful** briefing (`storage.latest_briefing()`), regardless of whether today's run failed.

### Configuration is the seam

The RSS source list lives in the SQLite `sources` table — `config/sources.yaml` is read **once** at first DB init via `db.seed_sources_from_yaml()` and ignored afterward. Add/remove/edit feeds via `/sources` in the UI (or `db.create_source` / `db.update_source` in code). The topic categories still live in `config/categories.yaml` and are reloaded on every run; adding a category is a YAML edit. The classifier prompt in `llm._classification_prompt()` enumerates whatever categories are loaded; the template colors badges from the same YAML.

`config.py` defines `Source`, `Category`, and `Settings` (config-time types — distinct from `models.py` which holds runtime data flowing through the pipeline). `Settings.from_env()` reads env vars with sensible defaults; the Docker compose file overrides `FLASK_HOST=0.0.0.0` so the local-dev default (`127.0.0.1`) stays safe.

### Settings live in SQLite

Runtime settings (LLM provider, base URL, model, schedule) live in `cache/its_briefing.db`. `.env` seeds the `settings` table the very first time the DB is created; after that, the `/settings` page in the UI is the source of truth. `FLASK_HOST`, `FLASK_PORT`, and `LOG_LEVEL` stay env-only because they bind at process start.

The DB also stores articles (with cross-day dedup via `id = sha256(link)[:16]`), briefings (one row per day plus a join table to articles, plus a `last_error` column for the most recent summary-call failure), sources (with health columns: `last_status`, `last_checked_at`, `last_error`, `last_diagnosis`), and a `generation_runs` log. Schema is defined in `db.py`'s `_SCHEMA_SQL` and applied idempotently via `init_schema()` on every startup. Schema version is tracked in `schema_version` and `init_schema()` runs forward migrations (currently v1→v2 added `sources`, v2→v3 added `briefings.last_error`).

Sources also have health-check helpers in `its_briefing/sources.py`: `health_check_one(source)` (HTTP GET + feedparser, mirrors `_fetch_one` semantics), `start_health_check_job` / `get_check_job` (background thread pool with an in-memory job registry, polled from the `/sources` page), and `diagnose_failure(...)` (on-demand LLM analysis of a failing feed). The daily fetch back-feeds source statuses into the same table, so the page reflects reality every morning even without a manual check.

Two LLM clients live in `llm.py`: `OllamaClient` (POST /api/chat, reads `data["message"]["content"]`) and `LMStudioClient` (POST /v1/chat/completions, reads `data["choices"][0]["message"]["content"]`). `make_client(settings)` selects per `settings.llm_provider`. Adding a third provider means adding a class with `chat(prompt) -> str` and `list_models() -> list[str]`.

**Per-section summarization.** `llm.build_summary()` produces the four executive-summary sections via four separate LLM calls, each fed only articles classified into that section's mapped categories (see `SECTION_CATEGORIES` in `llm.py`). This keeps each prompt comfortably under any reasonable context window — the previous monolithic prompt was overflowing LM Studio's default 4096 ctx for typical 50-article days. If a section call fails after one retry, that section becomes empty and the most recent error is captured into `briefings.last_error`, which the briefing page renders as an amber strip under the Executive Summary header. Total summary failure (every populated section failed) still falls back to `ExecutiveSummary.placeholder`. The function returns `tuple[ExecutiveSummary, Optional[str]]`.

### Article IDs are stable across runs

`Article.make_id(link)` returns `sha256(link)[:16]`. This deterministic derivation is what allows the executive summary's bullet citations (`bullet.article_ids`) to deep-link to articles in the rendered HTML via `#article-{id}` anchors.

## Tests

122 tests under `tests/`, all run via `pytest`. TDD-driven per module:

- **`test_fetch.py`** uses `tests/fixtures/sample_feed.xml` + `freezegun` for the 24h cutoff, `pytest-httpx` for the concurrent fetcher.
- **`test_llm.py`** mocks both Ollama (`/api/chat`) and LM Studio (`/v1/chat/completions`) endpoints via `pytest-httpx`. Most failure-mode tests are parametrized across both providers; verifies HTTP error, invalid JSON, unknown category, retry path, and `make_client()` dispatch.
- **`test_storage.py`** round-trips briefings through `db.py` against a `tmp_path` SQLite file.
- **`test_db.py`** exercises schema init, settings seed-from-env, settings update, article + briefing CRUD, and `generation_runs` log against a `tmp_path` DB.
- **`test_generate.py`** patches `fetch`, `llm`, and `storage` at the `its_briefing.generate.<module>.<name>` level (because `generate.py` does `from its_briefing import config, fetch, llm, storage` — module-style imports).
- **`test_config.py`** uses `monkeypatch` for env var defaults.

`test_app.py` covers the Flask routes (settings GET/POST, `/api/test-connection`) with the I/O modules patched. `scheduler.py` has no dedicated tests beyond import smoke — it is a thin wrapper over APScheduler.

## Deployment

Full deployment guide in `DEPLOYMENT.md`. The short version: the project ships with a multi-stage `Dockerfile` (Python 3.13-slim, non-root `app` user UID 1000), a `docker-compose.yml` that bind-mounts `config/` (read-only) and `cache/` (read-write) from `/srv/apps/its-briefing/` on the host, and an Nginx vhost template at `deploy/nginx-its-briefing.conf` (optional reverse proxy). The container publishes port 8089 on **all host interfaces** (`"8089:8089"` in `docker-compose.yml`) so the app is reachable directly from the LAN at `http://<host>:8089/`. There is no auth — restrict access via host firewall if the LAN is not fully trusted. To put Nginx back in front, change the port mapping to `"127.0.0.1:8089:8089"` and enable the vhost from `deploy/nginx-its-briefing.conf`.

The Dockerfile deliberately installs only the runtime deps in the builder stage (not `pip install .`) — the source is copied to `/app/its_briefing/` in the runtime stage, and `python -m its_briefing` finds it via cwd-on-`sys.path` under `python -m`. This keeps a single source of truth for the package and matches the way `Path(__file__).resolve().parent.parent` in `config.py`/`db.py` resolves to `/app`, lining up with the bind mounts (`config/` read-only, `cache/` read-write so the SQLite DB survives container restarts).

## Spec & plan documents

The project follows a brainstorm → spec → plan → implementation workflow. The completed cycles live under:

- `docs/superpowers/specs/2026-04-07-its-briefing-design.md` — original app design
- `docs/superpowers/plans/2026-04-07-its-briefing.md` — original implementation plan
- `docs/superpowers/specs/2026-04-07-its-briefing-docker-deployment-design.md` — Docker deployment design
- `docs/superpowers/plans/2026-04-07-its-briefing-docker-deployment.md` — Docker deployment plan
- `docs/superpowers/specs/2026-04-28-llm-provider-switching-design.md` — LLM provider switching design
- `docs/superpowers/plans/2026-04-28-llm-provider-switching.md` — LLM provider switching + DB-backed settings plan
- `docs/superpowers/specs/2026-04-29-sources-management-design.md` — sources page + per-section summarization design
- `docs/superpowers/plans/2026-04-29-sources-management.md` — sources page + per-section summarization plan

Read these before making non-trivial changes to understand the original constraints and decisions.
