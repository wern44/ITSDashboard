---
title: ITS-Briefing — Daily AI-Curated Cybersecurity Briefing
date: 2026-04-07
status: approved
---

# ITS-Briefing — Design Document

## 1. Purpose

A slim, standalone Python application that aggregates 19 curated cybersecurity RSS feeds, classifies the articles into topic categories, generates an AI-written executive summary once per day, and serves the result on a modern dark-mode web page. No database, no authentication, no external services beyond a local Ollama instance.

The target user is a CISO (or security analyst) who wants a single page they can open in the morning that gives them: a four-section structured executive summary at the top, and a chronological feed of all relevant articles from the last 24 hours below it.

## 2. Decisions Reference

| Decision | Choice |
|---|---|
| Briefing type | Hybrid: aggregator + automatic daily AI summary as overview |
| LLM provider | Local Ollama (no API costs, fully offline-capable) |
| Default model | `llama3.1:8b`, configurable via `.env` (`OLLAMA_MODEL`) |
| Generation trigger | APScheduler in-process, daily 06:00 `Europe/Berlin` |
| Web framework | Flask + Jinja2, Tailwind CSS via CDN |
| UI / summary language | English |
| Article time window | Strict last 24 hours |
| Summary structure | Four sections: Critical Vulnerabilities, Active Threats, Notable Incidents, Strategic / Policy |
| Article display | Single chronological feed, newest first, with category badges |
| Categories | Topic-based, defined in `config/categories.yaml`, extensible without code change |
| Article classification | Per-article LLM call to Ollama |
| Visual style | Dark slate-900 background, cyan-400 accents, security-ops aesthetic |
| Persistence | JSON files in `cache/`, one per day, no database |

## 3. Architecture Overview

ITS-Briefing is a single Python process. Flask serves the web UI and APScheduler runs the daily generation job in the same process. There is no database — generated briefings are persisted as JSON files in a `cache/` directory, one file per day (`briefing-YYYY-MM-DD.json`). When a user opens the page, Flask reads the JSON for "today" (or the latest available day) and renders it through a Jinja2 template.

```
                    ┌──────────────────────────────┐
                    │   ITS-Briefing (one process) │
                    │                              │
                    │   Flask  ─── serves ───┐     │
                    │     │                  │     │
                    │     │ reads            ▼     │
                    │     │            templates/  │
                    │     ▼                        │
                    │   cache/briefing-YYYY-MM-DD.json
                    │     ▲                        │
                    │     │ writes                 │
                    │     │                        │
                    │   APScheduler                │
                    │   (06:00 Europe/Berlin daily)│
                    │     │                        │
                    │     ▼                        │
                    │   pipeline:                  │
                    │   1. fetch RSS feeds         │
                    │   2. filter last 24h         │
                    │   3. classify via Ollama     │
                    │   4. summarize via Ollama    │
                    │   5. write JSON              │
                    └──────────────────────────────┘
```

The pipeline is also runnable as a CLI command (`python -m its_briefing.generate`), so a fresh briefing can be triggered manually without waiting for 06:00. The same code path is what the scheduler invokes — there is exactly one pipeline implementation.

## 4. Module Breakdown

Six small modules under `its_briefing/`, plus models. None should exceed ~150 lines.

```
its_briefing/
├── __init__.py
├── __main__.py        # `python -m its_briefing` → starts Flask + scheduler
├── app.py             # Flask app: routes + jinja env
├── scheduler.py       # APScheduler setup, registers daily job
├── generate.py        # Pipeline orchestrator (CLI entry + scheduler entry)
├── fetch.py           # RSS fetching + 24h filtering (uses feedparser)
├── llm.py             # Ollama client: classify_article() + build_summary()
├── storage.py         # Read/write briefing JSON files in cache/
├── config.py          # Loads sources.yaml, categories.yaml, .env
├── models.py          # Pydantic models: Article, Bullet, ExecutiveSummary, Briefing
└── templates/
    └── briefing.html
```

### Responsibilities

- **`config.py`** — Pydantic models for `Source`, `Category`, `Settings`. Loads YAML + env vars at startup. Single source of truth for configuration.
- **`models.py`** — Pydantic data classes for `Article`, `Bullet`, `ExecutiveSummary`, `Briefing` (see Section 5).
- **`fetch.py`** — `fetch_all(sources) -> tuple[list[Article], list[str]]`. Pure function. Concurrent feed fetching via `concurrent.futures.ThreadPoolExecutor` (max 10 workers) wrapping a sync `httpx.Client`. Per-feed timeout 10 s. Handles HTTP errors and malformed XML gracefully. Returns successfully parsed articles plus a list of failed source names.
- **`llm.py`** — Two functions:
  - `classify_article(article: Article, categories: list[Category]) -> str` — returns the chosen category name, or `"Uncategorized"` on failure.
  - `build_summary(articles: list[Article]) -> ExecutiveSummary` — single Ollama call returning structured JSON, validated through Pydantic.
  Both wrap HTTP calls to local Ollama at `${OLLAMA_BASE_URL}/api/chat`. No SDK dependency — just `httpx`. Use Ollama's `format: "json"` mode where structured output is needed.
- **`storage.py`** — `save_briefing(briefing: Briefing) -> Path`, `load_briefing(date: date) -> Briefing | None`, `latest_briefing() -> Briefing | None`. JSON files only. Creates the cache directory if missing.
- **`generate.py`** — Orchestrates the pipeline: `fetch → classify → summarize → save`. Logs progress. Callable from CLI (`python -m its_briefing.generate`) and from the scheduler. Wraps the whole run in a top-level try/except so a crash never propagates to the scheduler.
- **`scheduler.py`** — Wraps an APScheduler `BackgroundScheduler`, registers `generate.run()` as a `CronTrigger` at the configured hour/minute in `Europe/Berlin`. Started from `app.py` on Flask startup. Exposes `next_run_time()` for `/health`.
- **`app.py`** — Two routes plus one POST:
  - `GET /` — renders today's briefing (or the latest available)
  - `GET /health` — returns scheduler status, last successful build timestamp, next scheduled run
  - `POST /generate` — manually triggers a fresh build synchronously (used by the "rebuild now" button)

### Boundaries

`fetch`, `llm`, and `storage` are pure I/O modules with no Flask or scheduler dependency — trivially unit-testable. `generate.py` is the only place that knows the order of operations. `app.py` only knows about `storage.load_briefing()` and `generate.run()` — it never talks to Ollama or feedparser directly.

## 5. Data Model

Pydantic models in `its_briefing/models.py`:

```python
class Article:
    id: str                    # sha256(link)[:16] — stable across runs
    source: str                # "Bleeping Computer"
    source_lang: str           # "EN" | "DE"
    title: str
    link: str
    published: datetime        # UTC
    summary: str               # raw RSS summary/description, sanitized
    category: str | None       # filled in by llm.classify_article()

class Bullet:
    text: str                  # 1-2 sentences
    article_ids: list[str]     # links back to Article.id

class ExecutiveSummary:
    critical_vulnerabilities: list[Bullet]
    active_threats: list[Bullet]
    notable_incidents: list[Bullet]
    strategic_policy: list[Bullet]

class Briefing:
    date: date                 # the day this briefing represents
    generated_at: datetime     # UTC timestamp
    summary: ExecutiveSummary
    articles: list[Article]    # sorted desc by published
    failed_sources: list[str]  # names of sources that failed to fetch
    article_count: int
```

Article IDs are `sha256(link)[:16]` so they are stable across pipeline runs and can be used as anchor IDs in the rendered HTML, allowing the executive summary's bullet citations to deep-link to the articles below.

## 6. Pipeline Data Flow

The daily run in `generate.run()` executes these steps in order:

1. `sources = config.load_sources()` — loads the 19 RSS endpoints from `config/sources.yaml`.
2. `articles, failures = fetch.fetch_all(sources)` — concurrent fetch via `ThreadPoolExecutor` over a sync `httpx.Client`, filter to last 24 hours.
3. `categories = config.load_categories()` — loads category names + descriptions from `config/categories.yaml`.
4. **Classify loop:** for each article, `article.category = llm.classify_article(article, categories)`. Sequential calls to Ollama. Roughly 50 articles × 1–2 s each ≈ 1–2 minutes.
5. `summary = llm.build_summary(articles)` — single call with the full classified article corpus, returns a structured `ExecutiveSummary` (Ollama `format: "json"` mode + Pydantic validation).
6. Construct `Briefing(date=today, generated_at=now_utc, ...)` and call `storage.save_briefing(briefing)`.
7. Log: `"Briefing for 2026-04-07 generated: 47 articles, 2 failed sources"`.

On the read side (Flask):

- `GET /` → `briefing = storage.latest_briefing()` → `render_template("briefing.html", briefing=briefing)`.
- If no briefing exists yet (very first run before 06:00), the template shows a "Briefing not yet generated" placeholder with a button that POSTs to `/generate` and triggers `generate.run()` synchronously.

## 7. Configuration

Three configuration files in `config/` and at the project root. Editing them never requires touching Python code.

### `config/sources.yaml`

The 19 RSS feeds. CISA is excluded — its RSS feed was discontinued in May 2025. Adding a new source = append a block.

```yaml
sources:
  - name: "Bleeping Computer"
    url: "https://www.bleepingcomputer.com/feed/"
    lang: "EN"
  - name: "The Hacker News"
    url: "https://feeds.feedburner.com/TheHackersNews"
    lang: "EN"
  - name: "Dark Reading"
    url: "https://www.darkreading.com/rss.xml"
    lang: "EN"
  - name: "SecurityWeek"
    url: "https://feeds.feedburner.com/securityweek"
    lang: "EN"
  - name: "The Record"
    url: "https://therecord.media/feed"
    lang: "EN"
  - name: "Help Net Security"
    url: "https://www.helpnetsecurity.com/feed/"
    lang: "EN"
  - name: "Krebs on Security"
    url: "https://krebsonsecurity.com/feed/"
    lang: "EN"
  - name: "Schneier on Security"
    url: "https://www.schneier.com/feed/"
    lang: "EN"
  - name: "Graham Cluley"
    url: "https://grahamcluley.com/feed/"
    lang: "EN"
  - name: "Risky Business News"
    url: "https://risky.biz/feeds/risky-business-news"
    lang: "EN"
  - name: "Google Threat Intelligence"
    url: "https://feeds.feedburner.com/threatintelligence/pvexyqv7v0v"
    lang: "EN"
  - name: "CrowdStrike"
    url: "https://www.crowdstrike.com/blog/feed/"
    lang: "EN"
  - name: "Cisco Talos"
    url: "https://blog.talosintelligence.com/feeds/posts/default"
    lang: "EN"
  - name: "Sophos News"
    url: "https://news.sophos.com/en-us/feed/"
    lang: "EN"
  - name: "SANS ISC"
    url: "https://isc.sans.edu/rssfeed_full.xml"
    lang: "EN"
  - name: "heise Security"
    url: "https://www.heise.de/security/rss/alert-news-atom.xml"
    lang: "DE"
  - name: "golem.de Security"
    url: "https://rss.golem.de/rss.php?ms=security&feed=RSS1.0"
    lang: "DE"
  - name: "BSI / CERT-Bund WID"
    url: "https://www.bsi.bund.de/SiteGlobals/Functions/RSSFeed/RSSNewsfeed/RSSNewsfeed_WID.xml"
    lang: "DE"
  - name: "Borns IT-Blog"
    url: "https://www.borncity.com/blog/feed/"
    lang: "DE"
```

### `config/categories.yaml`

Topic categories used both for badges in the UI and as the label set passed to the Ollama classifier. Adding a category = append, no code change.

```yaml
categories:
  - name: "IT News daily"
    description: "General IT and tech industry news that is not security-specific"
    color: "#94a3b8"
  - name: "IT-Security"
    description: "Defensive security topics: best practices, frameworks, hardening, security operations"
    color: "#22d3ee"
  - name: "Cyber-Security"
    description: "Broader cyber news: industry, policy, geopolitics, nation-state activity"
    color: "#a78bfa"
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

### `.env`

Runtime/secret configuration. `.env.example` is committed; `.env` is gitignored.

```
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
TIMEZONE=Europe/Berlin
SCHEDULE_HOUR=6
SCHEDULE_MINUTE=0
FLASK_HOST=127.0.0.1
FLASK_PORT=8089
LOG_LEVEL=INFO
```

## 8. Error Handling

Three failure modes, each handled at its own boundary so a problem in one part of the pipeline never breaks the rest.

| Failure | Where caught | Behavior |
|---|---|---|
| Single RSS feed unreachable / malformed | `fetch.fetch_all()` | Log warning, append source name to `failed_sources`, continue with other feeds. **Never fails the pipeline.** |
| Ollama unreachable / returns garbage | `llm.classify_article()` and `llm.build_summary()` | Per article: tag `category = "Uncategorized"`, log warning. For summary: retry once, then fall back to a placeholder summary `"AI summary unavailable for {date} — see articles below."`. Pipeline still produces a usable briefing. |
| Pipeline crashes mid-run | `generate.run()` | Top-level try/except logs full traceback, scheduler keeps running. Previous day's briefing remains visible. |

A failed daily run never blocks the next day's run. The frontend always renders the **most recent successful** briefing. The `/health` endpoint exposes the last successful build timestamp so the user can spot stale data at a glance.

## 9. Frontend

A single Jinja2 template, `templates/briefing.html`. Layout from top to bottom:

1. **Header bar** — `ITS Briefing` title, date, "Generated at HH:MM UTC", a small badge showing `47 articles · 19 sources · 2 failed`. Failed source names listed in a tooltip on the failed badge.
2. **Executive Summary card** — four sections (Critical Vulnerabilities / Active Threats / Notable Incidents / Strategic) as bullet lists. Each bullet ends with small superscript numbers `[1][2]` linking to the relevant articles below (anchored by `article.id`).
3. **Article timeline** — chronological, newest first. Each article = a slim card containing:
   - Category badge (color from `categories.yaml`)
   - Source name + language badge
   - Title (large, links to original article)
   - Relative time ("3 hours ago"), absolute time on hover
   - First ~200 chars of `summary`
4. **Footer** — link to `/health`, "rebuild now" button (POST `/generate`).

### Visual Style

Dark mode security-ops aesthetic. Background `slate-900`, accent `cyan-400`, monospace font (JetBrains Mono via Google Fonts CDN) for CVE IDs and timestamps. Tailwind CSS via CDN — single `<script>` tag in `<head>`, no build step.

### JavaScript

Roughly 20 lines of vanilla JS, no framework, for:

- Timestamp localization via `Intl.RelativeTimeFormat`
- The "rebuild now" button: `fetch('/generate', {method: 'POST'})` + spinner + page reload on success

## 10. Project Layout

```
ITS_Briefing/
├── README.md
├── pyproject.toml             # uv / pip-installable
├── .env.example
├── .gitignore
├── docs/superpowers/specs/
│   └── 2026-04-07-its-briefing-design.md
├── config/
│   ├── sources.yaml
│   └── categories.yaml
├── cache/                     # gitignored, created at runtime
│   └── briefing-2026-04-07.json
├── its_briefing/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── scheduler.py
│   ├── generate.py
│   ├── fetch.py
│   ├── llm.py
│   ├── storage.py
│   ├── config.py
│   ├── models.py
│   └── templates/
│       └── briefing.html
└── tests/
    ├── test_fetch.py
    ├── test_llm.py
    ├── test_storage.py
    ├── test_generate.py
    └── fixtures/
        ├── sample_feed.xml
        └── sample_briefing.json
```

## 11. Dependencies

Runtime (in `pyproject.toml`):

```
flask>=3.0
feedparser>=6.0
httpx>=0.27          # Ollama client + concurrent feed fetching
apscheduler>=3.10
pydantic>=2.6
pyyaml>=6.0
python-dotenv>=1.0
```

Dev: `pytest`, `pytest-httpx`, `freezegun`.

Seven runtime dependencies total. No SQLAlchemy, no Celery, no Redis, no Anthropic SDK.

## 12. Run Commands

```bash
# Install
uv sync             # or: pip install -e .

# Run web app + scheduler
python -m its_briefing

# Force a fresh briefing build right now (CLI)
python -m its_briefing.generate

# Run tests
pytest
```

## 13. Testing Strategy

Test-driven per module, with fakes at I/O boundaries.

- **`test_fetch.py`** — feeds parsed from `fixtures/sample_feed.xml`, network mocked via `pytest-httpx`. Covers: 24h filter, malformed feed, 500 response, timeout. Uses `freezegun` to fix "now".
- **`test_storage.py`** — round-trip save/load with `tmp_path`. Covers: `latest_briefing()` picks the highest date, missing cache directory is created automatically.
- **`test_llm.py`** — Ollama HTTP call mocked via `pytest-httpx`. Covers: valid JSON parses to `ExecutiveSummary`, invalid JSON falls back to `"Uncategorized"` / placeholder summary.
- **`test_generate.py`** — full pipeline run with all I/O faked (fake fetcher, fake LLM, `tmp_path` for cache). Verifies the assembled `Briefing` object matches expectations.
- **No tests for `app.py` and `scheduler.py`** beyond a smoke test that the Flask app starts and `/` renders with a fixture briefing — they are thin wrappers over the tested modules.

## 14. Acceptance Criteria

1. `python -m its_briefing.generate` produces `cache/briefing-<today>.json` against real Ollama and real feeds.
2. `python -m its_briefing` serves the briefing on `http://127.0.0.1:8089`.
3. APScheduler logs the next scheduled run time at startup.
4. All unit tests pass.
5. Frontend renders cleanly on a 1080p screen in dark mode, including all four executive summary sections, article cards with category badges, and the rebuild button.
6. A failing RSS feed does not break the pipeline; the failed source appears in the header badge.
7. An unavailable Ollama produces a placeholder summary and `"Uncategorized"` tags; the page still renders.
