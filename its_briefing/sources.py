"""Source health checks and on-demand LLM diagnosis."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

import feedparser
import httpx

from its_briefing import db as _db
from its_briefing.config import Settings, Source
from its_briefing.llm import LLMClientError, _strip_code_fences, make_client

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


# ---------------------------------------------------------------------------
# Background job registry
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# On-demand LLM diagnosis
# ---------------------------------------------------------------------------


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
