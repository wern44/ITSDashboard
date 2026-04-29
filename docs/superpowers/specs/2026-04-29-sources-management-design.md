# Sources Management & Per-Section Summarization — Design

**Date:** 2026-04-29
**Status:** Approved (pending user review of this document)

## Context

Two pain points:

1. **No source visibility in the UI.** The 19 RSS feeds the briefing pulls from are defined in `config/sources.yaml`. When a feed breaks, today's briefing silently shows fewer articles and the user has no way to see which feeds failed without grepping logs. The 2026-04-28 run had four failed sources (Cisco Talos, BSI / CERT-Bund WID, Help Net Security, Sophos News) and the user only learned about it by inspecting the DB.

2. **The Executive Summary is falling back to "AI summary unavailable".** The 2026-04-28 briefing has only the placeholder bullet, with no explanation. Root cause: 56 articles in the single summary prompt × ~100 tokens each = ~5-6k tokens, which exceeds the LM Studio default 4096-token context for `google/gemma-4-26b-a4b`. The HTTP 400 with `n_keep > n_ctx` is captured by `LLMClientError` (commit `65f41f4`) but never reaches the UI — the user sees only the placeholder string.

This design solves both at once.

## Goals

- A `/sources` admin page that lists all sources, shows live health status (green/red/orange/disabled/never-checked), and lets the user check, edit, add, delete, enable/disable, and diagnose individual sources.
- Daily fetch failures back-feed status into the same table so the page reflects reality every morning without manual checking.
- "Diagnose" button on red sources sends the error to the configured LLM and returns a plain-language likely-cause + suggested fix.
- Replace the current single-prompt summary with per-section summarization driven by the existing classifier output, so the prompt is structurally bounded and the context-limit failure mode goes away.
- Surface the actual LLM error on the briefing page when summary attempts fail, so misconfiguration is visible.
- Add a `Phishing` category and replace the fuzzy `IT News daily` category with a sharper `Tech & Innovation` definition.

## Non-goals

- No new authentication. Single-user, LAN-only, same as today.
- No history of past health checks. Only the most recent status per source is stored.
- No automatic LLM diagnosis on every failure — diagnosis is on-demand only.
- No separate health-check cron. Health refreshes happen via (a) manual user action and (b) daily fetch back-feed.
- No changes to the article schema or how articles are persisted.

## Architecture

One new module `its_briefing/sources.py`, additions to `db.py`, additions to `app.py`, a new template `sources.html`, and a rewrite of `llm.build_summary()`.

```
its_briefing/
├── sources.py        # NEW: health_check_one(), health_check_all(), diagnose_failure()
├── db.py             # +sources table CRUD, +briefings.last_error column, +schema v3
├── config.py         # Source model gains `enabled: bool`; load_sources() reads from DB
├── fetch.py          # signature unchanged
├── generate.py       # +back-feeds source statuses after fetch_all()
├── llm.py            # build_summary() rewritten to per-section
├── app.py            # +/sources GET, +/api/sources/* JSON endpoints
└── templates/
    └── sources.html  # NEW: dense table + inline expander + edit modal
```

The boundary rule from `CLAUDE.md` is preserved: `sources.py` is pure I/O (HTTP + DB), no Flask, no scheduler. It's trivially unit-testable just like `fetch.py` and `llm.py`.

## Data model

### New table: `sources`

```sql
CREATE TABLE sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    url             TEXT NOT NULL,
    lang            TEXT NOT NULL,            -- "EN" | "DE"
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_status     TEXT,                     -- "ok" | "failed" | "checking" | NULL
    last_checked_at TEXT,                     -- ISO8601 UTC
    last_error      TEXT,                     -- HTTP code / parse msg / exception
    last_diagnosis  TEXT,                     -- LLM analyzer output, cleared on status change
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX idx_sources_enabled ON sources(enabled);
```

### Modified table: `briefings`

Add one column:

```sql
ALTER TABLE briefings ADD COLUMN last_error TEXT;
```

Holds the most recent `LLMClientError` message from the failing summary attempt(s). Rendered as a small red strip on the briefing page when present and non-empty.

### Schema migrations

- **v1 → v2:** create `sources` table, seed from `config/sources.yaml` if the table is empty.
- **v2 → v3:** `ALTER TABLE briefings ADD COLUMN last_error`.

`db.init_schema()` checks `schema_version` and applies upgrades idempotently. Both migrations are pure-additive.

### YAML → DB transition

`config/sources.yaml` becomes a one-time seed (parallel to how `.env` seeds settings). On first DB init at v2, if `sources` is empty, `seed_sources_from_yaml()` parses the YAML and inserts each row. After seeding, the YAML file is ignored. We keep it in the repo for one release as a backup, then remove it in a follow-up cleanup.

`config.load_sources()` keeps its existing signature but is rewritten to read from the DB. Existing tests that pass an explicit `path: Path` argument continue to work via a thin compatibility shim that reads the YAML file when a path is given.

### `Source` Pydantic model

`config.Source` gains:

```python
enabled: bool = True
last_status: Optional[str] = None
last_checked_at: Optional[datetime] = None
last_error: Optional[str] = None
last_diagnosis: Optional[str] = None
```

The fetch pipeline still uses only `name`/`url`/`lang`. The health fields are read by the sources page only.

## Routes

All under `app.py`:

| Method | Path | Body / Query | Purpose |
|---|---|---|---|
| `GET` | `/sources` | — | Render `sources.html` with full source list + statuses |
| `GET` | `/api/sources` | — | JSON list (used by polling) |
| `POST` | `/api/sources` | `{name, url, lang, enabled}` | Create one source |
| `PATCH` | `/api/sources/<id>` | partial `{name?, url?, lang?, enabled?}` | Update fields |
| `DELETE` | `/api/sources/<id>` | — | Delete a source |
| `POST` | `/api/sources/<id>/check` | — | Kick off single-source check, returns `{job_id}` |
| `POST` | `/api/sources/check-all` | — | Kick off check across all enabled sources, returns `{job_id}` |
| `GET` | `/api/sources/check-status?job_id=...` | — | Poll: `{state: "running"|"done", results: [...]}` |
| `POST` | `/api/sources/<id>/diagnose` | — | Synchronous LLM analysis; returns `{suggestion, error?}`, caches into `last_diagnosis` |

The existing gear-icon link in `briefing.html` becomes a small dropdown with: **Settings**, **Sources**, `/health`.

## Health check semantics

A check on one source = HTTP GET (`follow_redirects=True`, 10s timeout) + `feedparser.parse()`. Status:

- **ok** if HTTP 2xx **and** parser produced at least one entry.
- **failed** otherwise. The `last_error` field gets a short message:
  - `HTTP <code>` for non-2xx
  - `connection: <type>` for timeouts/DNS/TLS
  - `parse: no entries` if the response parsed to zero entries
  - `parse: <feedparser bozo_exception>` on malformed XML

This matches what `_fetch_one()` already does, so health-check status reliably predicts briefing-time fetch behavior.

### Concurrency model

Re-uses APScheduler's existing `BackgroundScheduler` thread pool. A check job is an in-memory dict keyed by a `job_id` (UUID4 hex):

```python
{
  "state": "running" | "done",
  "started_at": datetime,
  "source_ids": list[int],
  "results": dict[int, {"status": str, "error": str | None}],
}
```

While running, the affected `sources.last_status` is set to `"checking"` so the UI dot turns orange via the poll. Final results are persisted into the `sources` row (`last_status`, `last_checked_at`, `last_error`, and clear `last_diagnosis` if the status changed). Job dicts are GC'd after 5 minutes; persistent state is in the DB.

`MAX_WORKERS = 10` (matches `fetch.py`). 19 sources × 10s timeout in 10 workers worst-case ≈ 20s.

### Daily fetch back-feed

`generate.run()` writes source statuses after `fetch_all()` returns. For each configured source: `last_status = "ok"` if the source is not in `failed_sources`, else `"failed"` with `last_error` reproducing the same short message. Single transaction. Wrapped in its own try/except so a DB write failure here doesn't lose articles.

`generate.run()` also switches from `config.load_sources()` to `db.list_sources(enabled_only=True)` so disabled sources are skipped. Disabled sources retain their last status row but are never fetched.

## Diagnose endpoint

Synchronous LLM call. Body of the prompt:

```
A cybersecurity-news RSS feed is failing. Diagnose it briefly.

Source name: {name}
URL: {url}
Last error: {last_error}
HTTP status / exception type if known: {short}

Respond as JSON: {"likely_cause": "...", "suggested_fix": "..."}
Be concise: one sentence per field.
```

Uses `llm.make_client(settings)` — same provider/model as the briefing. Failure of the diagnose call returns `{"suggestion": null, "error": "<reason>"}`; the UI shows "couldn't analyze: <reason>" in the expander instead of a suggestion.

The result is stored in `sources.last_diagnosis` (concatenated `cause — fix`). It's cleared whenever `last_status` flips, so a recovered source doesn't show stale advice.

## UX — `sources.html`

### Header

```
[Sources]                             [● 15 ok  ● 4 failed  ○ 0 checking]   [+ Add]  [Check all]
```

The three counts double as click-to-filter chips ("show only failed").

### Table columns

| Status | Name | URL | Lang | Last checked | Last error | Actions |
|---|---|---|---|---|---|---|

- **Status:** colored dot. 🟢 ok / 🔴 failed / 🟠 checking / ⚫ disabled / ⚪ never checked.
- **Name:** plain text.
- **URL:** truncated (CSS `overflow: ellipsis`); full URL in a tooltip.
- **Lang:** small mono badge (`EN` / `DE`), same style as the briefing page.
- **Last checked:** humanized relative time ("2 min ago", "14h ago", "never"). Tooltip = full ISO timestamp.
- **Last error:** one short line; full message in the inline expander.
- **Actions:** `Check`, `Edit`, `⋯` (Disable/Enable + Delete). Failing rows additionally show `Analyze ▾`.

### Analyze expander

Slide-down row beneath the failing source:

```
Cisco Talos — Diagnosis
Error: HTTP 503 from blog.talosintelligence.com (Service Unavailable)

🤖 Likely cause: temporary CDN throttling or planned downtime.
   Suggested fix: retry in 1-2h; if still failing, check
   talosintelligence.com/blog/rss for a new feed URL.

[Re-check]  [Open feed in browser]  [Close]
```

When a cached `last_diagnosis` exists, render it immediately and show a `Re-analyze` button. If empty, the expander shows a small spinner during the LLM call (~5-15s on a local model).

### Edit / Add modal

Small dialog. Fields: `Name` (text), `URL` (text), `Lang` (radio EN/DE), `Enabled` (checkbox). Buttons: `Save`, `Cancel`. Validation: name non-empty + unique, URL non-empty + parses with `urllib.parse`, lang ∈ {EN, DE}.

### Polling

While any check job is `running`, JS polls `/api/sources/check-status?job_id=...` every 1s, updating dot colors and `last_checked` cells live. Stops when `state === "done"`.

### Empty state

First run before any check: dots are ⚪, `last_checked = "never"`. A subtle banner: *"Sources have not been checked yet — [Check all] to verify."*

### Mobile

Below 768px the table collapses to stacked cards (Tailwind `md:` breakpoints). Same data, less dense. Action buttons stack vertically on the card.

### Header navigation

The existing single ⚙ icon in `briefing.html` becomes a small dropdown:

- **Settings** → `/settings`
- **Sources** → `/sources`
- **/health** → `/health`

## Per-section summarization (the "AI summary unavailable" fix)

`llm.build_summary()` is rewritten. The four output sections are produced by four separate LLM calls, each fed only articles in the relevant categories:

```python
SECTION_CATEGORIES: dict[str, list[str]] = {
    "critical_vulnerabilities": ["Threats and Vulnerabilities", "0-Day"],
    "active_threats":           ["0-Day", "Hacks", "Phishing"],
    "notable_incidents":        ["Hacks"],
    "strategic_policy":         ["Regulation", "Cyber-Security", "IT-Security"],
}
```

For each section:

1. Filter `articles` to those whose `category` is in `SECTION_CATEGORIES[section]`.
2. If the filtered list is empty, the section result is `[]` (no LLM call).
3. Otherwise, call `_try_build_section(articles_subset, section)` which prompts the LLM to produce **only** `list[Bullet]` for that one section.
4. On `LLMClientError` / `JSONDecodeError` / `ValidationError`, retry once. On second failure, the section becomes `[]` and the error message is captured.

Stitch the four `list[Bullet]` results into one `ExecutiveSummary`. Articles classified as `Tech & Innovation`, `Uncategorized`, or any unmapped category stay out of the summary but appear in the article list below.

### Error visibility

After all four section calls complete, if any of them failed even after retry, capture the most recent `LLMClientError` message and store it on `briefings.last_error` for that day. The briefing template renders it under the Executive Summary header:

```
⚠ Last summary attempt failed: HTTP 400 from /v1/chat/completions:
  {"error":"Trying to keep the first 5132 tokens when context the
  overflows. Please load a model with a larger context length, or
  request a smaller prompt."} — section may be incomplete.
```

If **all four** sections failed (the rare total-blackout case), the existing `ExecutiveSummary.placeholder(target_date)` is still emitted so the page is never blank.

### Latency

4 LLM calls instead of 1, sequentially. On a local LM Studio with gemma, each section call is ~5-15s with 5-15 articles. Total ≈ 20-60s vs. the current single-call ~10-30s. Acceptable: the briefing is generated once per day at 06:00, so a 30s-1min increase is invisible.

## Categories update

`config/categories.yaml` becomes:

```yaml
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

Changes vs. current:

- `IT News daily` → replaced by `Tech & Innovation` (sharper definition).
- `Phishing` added.

The classifier prompt in `llm._classification_prompt()` already enumerates whatever categories `load_categories()` returns, so no code change there.

Articles previously classified as `IT News daily` will be left in the DB with that label until the next run; new articles use the new categories. Briefing template colors badges from the YAML, so existing rows render with the default grey if `IT News daily` is no longer in the YAML — acceptable for stale rows.

## Error handling philosophy

Same three-layer model as today, extended:

1. **Single feed unreachable / malformed** → `_fetch_one()` swallows + adds to `failed_sources`. New: `generate.run()` back-feeds that into `sources.last_status`.
2. **LLM unreachable / garbage** → per-section: retry once, fall back to `[]` for that section, capture error to `briefings.last_error`. Total-blackout fallback is the existing placeholder.
3. **Pipeline crashes** → top-level `try/except` in `generate.run()` is preserved.
4. **Health check job partial failure** → individual source results are persisted regardless; sources whose check didn't finish stay in their previous state.
5. **Diagnose endpoint failure** → returns `{"suggestion": null, "error": "..."}`; UI renders an inline error message.
6. **Source CRUD invalid input** → 400 with a JSON `{"errors": [...]}` body; the modal renders them inline.

## Testing strategy

Following the existing TDD pattern (`tests/test_*.py`, mocking external I/O at the boundary).

### `test_db.py` (extend)

- `test_seed_sources_from_yaml_when_empty`
- `test_seed_sources_from_yaml_idempotent`
- `test_list_sources_enabled_only`
- `test_create_source_unique_name`
- `test_update_source_partial`
- `test_delete_source`
- `test_record_source_check_result_clears_diagnosis_on_status_change`
- `test_briefings_last_error_roundtrip`
- `test_schema_v1_to_v2_migration_creates_sources_and_seeds`
- `test_schema_v2_to_v3_migration_adds_briefings_last_error`

### `test_sources.py` (new)

- `test_health_check_one_returns_ok_on_2xx_with_entries` (`pytest-httpx`)
- `test_health_check_one_returns_failed_on_5xx`
- `test_health_check_one_returns_failed_on_zero_entries`
- `test_health_check_one_returns_failed_on_timeout`
- `test_health_check_all_persists_results`
- `test_health_check_all_marks_orange_during_run`
- `test_diagnose_failure_calls_llm_with_error_context`
- `test_diagnose_failure_returns_error_when_llm_fails`

### `test_llm.py` (extend)

- `test_build_summary_per_section_only_calls_relevant_articles` (verify category filter)
- `test_build_summary_one_section_failure_does_not_break_others`
- `test_build_summary_all_sections_failed_returns_placeholder`
- `test_build_summary_records_last_error_on_partial_failure`
- `test_build_summary_empty_category_skips_llm_call`

### `test_app.py` (extend)

- Routes for sources CRUD, check, check-all, check-status, diagnose. Mock `sources.health_check_*` and `sources.diagnose_failure` at the `its_briefing.app.<module>.<name>` level (note: `app.py` does inline imports today; for testability the new endpoints should import at module top so the standard patch pattern works).

### `test_generate.py` (extend)

- `test_run_back_feeds_source_statuses_after_fetch`
- `test_run_uses_only_enabled_sources`

### `test_config.py` (extend)

- `test_source_model_enabled_default_true`

## Open questions answered (recap)

- **YAML vs DB:** DB-backed sources, YAML is one-time seed only.
- **Health-check semantics:** reachable + parseable (zero entries = failed).
- **Diagnose trigger:** on-demand, never automatic.
- **Concurrency model:** background job + 1s polling, returns job_id.
- **CRUD scope:** add, edit, delete, enable/disable.
- **Layout:** dense table with inline analyze expander.
- **Auto-checks:** none, but daily fetch back-feeds status.
- **Summary fix:** per-section summarization (D) + surfaced errors (A).
- **Categories:** add `Phishing`, replace `IT News daily` with `Tech & Innovation`.

## Risks & mitigations

- **Seeding race on first boot.** If two processes init the schema simultaneously and both find an empty `sources` table, both could insert duplicates. Mitigation: `INSERT ... ON CONFLICT(name) DO NOTHING` so duplicate-name inserts are no-ops; the `sources.name UNIQUE` index enforces correctness.
- **YAML drift after seed.** Once seeded, edits to `config/sources.yaml` are ignored. Mitigation: a comment at the top of the YAML file noting it's a one-time seed; document in `CLAUDE.md`.
- **Stale `IT News daily` articles.** Existing articles in the DB keep their old category. Mitigation: accept it — the classifier rebuilds the taxonomy on every new article, and the dashboard/badge code falls back to grey for unknown categories.
- **Latency of 4-call summarization.** Worst case ~60s on slow local models. Mitigation: only matters in the manual `POST /generate` path; cron path is fine. The briefing template already shows "Generating…" for that button.
- **APScheduler thread-pool pressure.** Health checks share the pool with the daily cron. Mitigation: the cron runs once a day and a check job lasts ~20s, so contention is theoretical.

## Out of scope

- Per-source check history & charts.
- Rate-limit / throttle handling beyond a single retry.
- Automatic LLM diagnosis on every failure.
- Multi-language UI.
- Authentication.
- Bulk import / OPML.
